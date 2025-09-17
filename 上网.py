"""
Web Crawler Module for Sese Search Engine

This module implements a BFS-based web crawler that:
1. Crawls web pages and extracts content
2. Indexes pages directly to Meilisearch 
3. Manages website information and statistics
4. Handles quality scoring and link analysis
"""

import math
import json
import time
import random
import socket
import hashlib
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Iterable, Callable, Dict, Hashable, Any

import tldextract

from 打点 import tqdm, tqdm面板, timing_tick, 直方图打点
import 分析
import 信息
from 文 import shrink_url, get_desc
from 网站 import map_website, Website
from 配置 import (
    爬取线程数, 爬取集中度, 单网页最多关键词, 入口, 最大epoch,
    预期繁荣网站比例, meilisearch_batch_size
)
from utils import tqdm_exception_logger, 坏, detect_lang, netloc, html结构特征
from meilisearch_client import meilisearch_client

# Global monitoring and tracking setup
progress_panel = tqdm面板([
    'visited_urls', 'successful_visits', 'domain_info_fetches',
    'extracted_keywords', 'english_keywords', 'pending_documents',
    'index_successes', 'index_failures', 'crawler_threads', 'current_epoch_progress'
])

prosperity_histogram = 直方图打点('url_prosperity_distribution',
                                  [0, 0.1, 0.3, 0.7, 1.5, 3.1, 6.3, 12, 25, 50, 100, 200, 400, 800, 1600, float("inf")])

domain_distribution_histogram = 直方图打点('url_domain_distribution',
                                           [1, 2, 3, 5, 7, 11, 17, 25, 38, 57, 86, 129, 194, 291, 437, 656,
                                            float("inf")])

# Global state
prosperity_cache = 信息.prosperity_data()
pending_index_queue = []
index_lock = threading.Lock()


def batch_index_documents():
    """
    Batch index documents to Meilisearch when queue reaches threshold size.
    Thread-safe operation that processes documents in batches for efficiency.
    """
    global pending_index_queue

    with index_lock:
        if len(pending_index_queue) >= meilisearch_batch_size:
            # Extract batch to process
            batch_documents = pending_index_queue[:meilisearch_batch_size]
            pending_index_queue = pending_index_queue[meilisearch_batch_size:]

            try:
                task = meilisearch_client.batch_add_documents(batch_documents)
                if task:
                    progress_panel['index_successes'].update(len(batch_documents))
                else:
                    progress_panel['index_failures'].update(len(batch_documents))
            except Exception as e:
                tqdm_exception_logger(e)
                progress_panel['index_failures'].update(len(batch_documents))


def add_document_to_index(url: str, title: str, description: str, text: str, keywords: List[Tuple[str, float]]):
    """
    Add a document to the indexing queue for batch processing.

    Args:
        url: The webpage URL
        title: Page title
        description: Page description/meta description
        text: Main text content
        keywords: List of (keyword, weight) tuples
    """
    global pending_index_queue

    domain = netloc(url)

    # Get language from website info
    website_info = map_website[domain]
    language = 'zh'  # Default to Chinese
    if website_info.lang_type:
        language = max(website_info.lang_type.items(), key=lambda x: x[1])[0]

    # Get prosperity score
    prosperity_score = 信息.prosperity(url)

    document = {
        'url': url,
        'title': title,
        'description': description,
        'text': text,
        'keywords': [kw[0] for kw in keywords],  # Extract only keywords, not weights
        'domain': domain,
        'language': language,
        'https_available': url.startswith('https://'),
        'prosperity': prosperity_score
    }

    with index_lock:
        pending_index_queue.append(document)
        progress_panel['pending_documents'].n = len(pending_index_queue)
        progress_panel['pending_documents'].refresh()

    # Check if batch processing is needed
    if len(pending_index_queue) >= meilisearch_batch_size:
        batch_index_documents()


@timing_tick
def fetch_page_content(url: str) -> Tuple[str, str, str, List[str], str, Dict[str, str], str, str]:
    """
    Fetch and process webpage content with redirect handling.

    Args:
        url: Target URL to fetch

    Returns:
        Tuple of (title, description, text, href_list, final_url, redirects, raw_html, server_type)
    """
    page_info = get_desc(url, timeout=10)

    # Skip processing for very long URLs to avoid issues
    if len(url) >= 250:
        return page_info

    title, description, text, href_list, final_url, redirect_data, raw_html, server_type = page_info

    # Process redirect information
    if redirect_data:
        for source_url, target_url in redirect_data.items():
            domain = netloc(source_url)
            website_info = map_website[domain]
            website_info.redirect[source_url] = target_url

            # Limit redirect history size
            if len(website_info.redirect) > 50:
                # Keep most recent 40 entries, prioritizing homepage
                sorted_redirects = sorted(website_info.redirect.items(),
                                          key=lambda x: random.random() - (x[0] == f'https://{domain}/'))
                website_info.redirect = dict(sorted_redirects[:40])

            map_website[domain] = website_info

    # Extract and process keywords
    keyword_list = 分析.龙(title, description, text)
    if keyword_list:
        # Sort by weight and limit to max keywords
        keyword_list = sorted(keyword_list, key=lambda x: x[1], reverse=True)[:单网页最多关键词]
        progress_panel['extracted_keywords'].update(len(keyword_list))
        progress_panel['english_keywords'].update(len([x for x in keyword_list if x[0].isascii()]))

        # Add to Meilisearch index
        add_document_to_index(final_url, title, description[:256], text[:256], keyword_list)

    return page_info


def reload_website_info(domain: str, website: Website):
    """
    Reload and update website information including quality metrics.

    Args:
        domain: Domain name to update
        website: Website object to update
    """
    try:
        # Update missing basic info
        if any(attr is None for attr in [website.quality, website.feature, website.keywords,
                                         website.https_valid, website.structure]):
            quality, feature, keywords, https_valid, structure, server_type = get_domain_info(domain)

            if website.quality is None:
                website.quality = quality
            if website.feature is None:
                website.feature = feature
            if website.keywords is None:
                website.keywords = keywords
            if not website.https_valid:
                website.https_valid = https_valid
            if website.structure is None:
                website.structure = structure
            if server_type and not website.server_type:
                website.server_type = [server_type]

    except Exception as e:
        tqdm_exception_logger(e)

    try:
        # Update IP information if missing
        if website.ip is None:
            website.ip = [addr[4][0] for addr in socket.getaddrinfo(domain, 443, 0, 0, socket.SOL_TCP)][:3]
    except Exception as e:
        tqdm_exception_logger(e)


def get_domain_info(domain: str) -> tuple[float, tuple[int, str, int], list[str], bool, str, Any]:
    """
    Fetch comprehensive information about a domain.

    Args:
        domain: Domain name to analyze

    Returns:
        Tuple of (quality_score, feature_hash, keywords, https_valid, structure, server_type)
    """
    progress_panel['domain_info_fetches'].update(1)

    try:
        # Try HTTPS first
        title, description, text, href_list, final_url, redirects, raw_html, server_type = fetch_page_content(
            f'https://{domain}/')
        https_valid = True
    except Exception:
        # Fall back to HTTP
        title, description, text, href_list, final_url, redirects, raw_html, server_type = fetch_page_content(
            f'http://{domain}/')
        https_valid = False

    # Calculate quality score
    quality_score = 1.0
    if not title:
        quality_score *= 0.2
    if not description:
        quality_score *= 0.7
    if not https_valid:
        quality_score *= 0.8
    if 'm' in domain.split('.'):  # Mobile subdomain penalty
        quality_score *= 0.6

    # Generate content features
    combined_text = ' '.join([title, description, text])
    encoded_text = combined_text.encode('utf8')
    content_feature = len(combined_text), hashlib.md5(encoded_text).hexdigest(), sum(encoded_text)

    # Extract HTML structure signature
    html_structure = html结构特征(raw_html)

    # Extract top keywords
    top_keywords = [kw[0] for kw in sorted(分析.龙('', '', text), key=lambda x: -x[1])[:40]]

    return quality_score, content_feature, top_keywords, https_valid, html_structure, server_type


def crawl_single_url(url: str) -> List[str]:
    """
    Crawl a single URL and return found links.

    Args:
        url: URL to crawl

    Returns:
        List of found URLs to crawl next
    """
    progress_panel['visited_urls'].update(1)
    progress_panel['current_epoch_progress'].update(1)

    try:
        # Track prosperity distribution
        try:
            prosperity_histogram.observe(prosperity_cache.get(netloc(url), 0))
        except Exception as e:
            tqdm_exception_logger(e)

        try:
            title, description, text, href_list, final_url, redirects, raw_html, server_type = fetch_page_content(url)
        except Exception as e:
            # Handle failed requests
            domain = netloc(url)
            website_info = map_website[domain]

            # Update IP info if missing
            if website_info.ip is None:
                try:
                    website_info.ip = [addr[4][0] for addr in socket.getaddrinfo(domain, 443, 0, 0, socket.SOL_TCP)][:3]
                except:
                    pass

            # Update success rate
            if website_info.success_rate is None:
                website_info.success_rate = 0
            website_info.success_rate *= 0.99  # Decay success rate
            map_website[domain] = website_info
            raise e
        else:
            # Successful crawl
            progress_panel['successful_visits'].update(1)
            domain = netloc(final_url)
            shrunk_url = shrink_url(final_url)

            # Update website statistics
            website_info = map_website[domain]
            website_info.visit_count += 1
            website_info.last_visit_time = int(time.time())

            if website_info.success_rate is None:
                website_info.success_rate = 1
            if final_url.startswith('https://'):
                website_info.https_valid = True
            website_info.success_rate = website_info.success_rate * 0.99 + 0.01

            reload_website_info(domain, website_info)

            try:
                # Update server type info
                if server_type and server_type not in website_info.server_type:
                    website_info.server_type.append(server_type)
                    website_info.server_type = website_info.server_type[-5:]  # Keep recent 5

                # Update language detection and external links periodically
                if website_info.visit_count < 10 or random.random() < 0.1:
                    detected_language = detect_lang(' '.join((title, description, text)))
                    update_intensity = min(0.2, 1 / (website_info.visit_count ** 0.5))

                    # Update language distribution
                    updated_lang_dist = {k: v * (1 - update_intensity) for k, v in website_info.lang_type.items()}
                    updated_lang_dist[detected_language] = updated_lang_dist.get(detected_language,
                                                                                 0) + update_intensity
                    website_info.lang_type = updated_lang_dist

                    # Sample external links
                    external_links = [link for link in href_list if shrink_url(link) != shrunk_url]
                    website_info.link += random.sample(external_links, min(10, len(external_links)))
                    if len(website_info.link) > 250:
                        website_info.link = random.sample(website_info.link, 200)

            except Exception as e:
                tqdm_exception_logger(e)

            map_website[domain] = website_info

            # Update shrunk URL statistics
            if shrunk_url != domain:
                shrunk_website_info = map_website[shrunk_url]
                shrunk_website_info.visit_count += 0.2
                reload_website_info(shrunk_url, shrunk_website_info)
                map_website[shrunk_url] = shrunk_website_info

            # Limit and prioritize found links
            if len(href_list) > 100:
                external_links = [link for link in href_list if shrink_url(link) != shrunk_url]
                href_list = random.sample(href_list, 100)
                if external_links:
                    href_list += random.sample(external_links, min(len(external_links), 3))

            return href_list

    except Exception as e:
        tqdm_exception_logger(e)
        time.sleep(0.2)
        return []


def deduplicate_urls(hash_func: Callable[[str], Hashable], url_list: Iterable[str], diversity_factor: float) -> List[
    str]:
    """
    Deduplicate and diversify URL list based on hash function.

    Args:
        hash_func: Function to generate hash for grouping
        url_list: Input URL list
        diversity_factor: Factor controlling diversity (higher = more diverse)

    Returns:
        Deduplicated and diversified URL list
    """
    grouped_urls = {}
    shuffled_urls = list(url_list)
    random.shuffle(shuffled_urls)

    # Group URLs by hash
    for url in shuffled_urls:
        grouped_urls.setdefault(hash_func(url), []).append(url)

    # Calculate per-group limit
    upper_limit = 10
    if len(grouped_urls) > 1:
        group_sizes = [int(len(urls) ** diversity_factor) for urls in grouped_urls.values()]
        upper_limit = max(10, int(sum(sorted(group_sizes)[:-1]) * 0.6))

    # Select URLs from each group
    result_urls = []
    for url_group in grouped_urls.values():
        selection_size = 1 + min(upper_limit, int(len(url_group) ** diversity_factor))
        result_urls += url_group[:selection_size]

    random.shuffle(result_urls)
    return result_urls


def prioritize_urls(url_weight_list: List[Tuple[str, float]]) -> List[str]:
    """
    Prioritize URLs based on multiple factors including prosperity, quality, and interest.

    Args:
        url_weight_list: List of (url, base_weight) tuples

    Returns:
        Prioritized list of URLs for crawling
    """

    def calculate_interest(domain: str, visit_count: int) -> float:
        """Calculate crawling interest based on prosperity and visit history"""
        limit = prosperity_cache.get(domain, 0) * 500 + 50
        decay_base = 0.1 ** (1 / limit)
        return decay_base ** visit_count

    def calculate_preference(url_weight_item: Tuple[str, float]) -> float:
        """Calculate overall preference score for URL"""
        url, base_weight = url_weight_item
        domain = netloc(url)
        website_info = cached_website_info[domain]

        # Calculate Chinese content ratio
        if website_info.lang_type:
            chinese_ratio = website_info.lang_type.get('zh', 0) / sum(website_info.lang_type.values())
        else:
            chinese_ratio = 0.5

        visit_count = website_info.visit_count
        quality = website_info.quality or 1
        shrunk_domain = shrink_url(url)

        # Calculate interest for both domain and shrunk domain
        domain_interest = calculate_interest(domain, visit_count)
        if shrunk_domain == domain:
            shrunk_interest = 1
        else:
            shrunk_website_info = cached_website_info[shrunk_domain]
            shrunk_visit_count = shrunk_website_info.visit_count
            shrunk_interest = calculate_interest(shrunk_domain, shrunk_visit_count)

        # Calculate prosperity factor
        prosperity = min(62, prosperity_cache.get(domain, 0))
        if prosperity > 0:
            prosperity += 0.5
        prosperity_factor = math.log2(2 + prosperity) + 1

        # Calculate bad URL penalty
        url_quality_factor = 1 - 坏(url)

        # Combine all factors
        preference_score = ((0.1 + chinese_ratio) *
                            min(0.05 + domain_interest, 0.05 + shrunk_interest) *
                            quality *
                            url_quality_factor *
                            base_weight *
                            prosperity_factor)

        return preference_score

    # Limit input size for performance
    if len(url_weight_list) > 100000:
        url_weight_list = random.sample(url_weight_list, 100000)

    # Cache website info for all domains
    all_urls = [url for url, weight in url_weight_list]
    all_domains = {netloc(url) for url in all_urls} | {shrink_url(url) for url in all_urls}

    with ThreadPoolExecutor(max_workers=16) as pool:
        cached_website_info = {domain: info for domain, info in
                               zip(all_domains, pool.map(map_website.get, all_domains))}

    # Select URLs based on preference scores
    selected_urls = random.choices(
        url_weight_list,
        weights=list(map(calculate_preference, url_weight_list)),
        k=min(45000, len(url_weight_list) // 3 + 250)
    )

    unique_urls = {url for url, weight in selected_urls}

    # Apply deduplication with diversity
    result_urls = deduplicate_urls(
        lambda url: tldextract.extract(url).domain,
        unique_urls,
        爬取集中度
    )

    # Separate HTTPS and HTTP URLs
    https_urls = [url for url in result_urls if url.startswith('https://')]
    http_urls = [url for url in result_urls if not url.startswith('https://')]

    # Limit HTTP URLs to maintain HTTPS preference
    if len(http_urls) > len(https_urls) // 4:
        http_urls = random.sample(http_urls, len(https_urls) // 4)

    combined_urls = http_urls + https_urls

    # Separate by prosperity status
    prosperous_urls = [url for url in combined_urls if 信息.prosperity(url)]
    non_prosperous_urls = [url for url in combined_urls if not 信息.prosperity(url)]

    # Balance prosperous vs non-prosperous URLs
    target_non_prosperous = int(len(prosperous_urls) * (1 - 预期繁荣网站比例) / 预期繁荣网站比例)
    if len(non_prosperous_urls) > target_non_prosperous:
        keep_count = max(len(non_prosperous_urls) - len(combined_urls) // 10, target_non_prosperous)
        non_prosperous_urls = random.sample(non_prosperous_urls, keep_count)

    final_urls = prosperous_urls + non_prosperous_urls
    random.shuffle(final_urls)
    return final_urls


# Global statistics tracking
crawl_statistics = []


def breadth_first_search(start_url: str, max_epochs=最大epoch):
    """
    Main BFS crawling loop that discovers and processes web pages.

    Args:
        start_url: Starting URL for crawling
        max_epochs: Maximum number of crawling epochs
    """
    visited_urls = set()
    current_queue = [start_url]

    for epoch in tqdm(range(max_epochs), ncols=60, desc='BFS Epochs'):
        visited_urls.update(current_queue)
        new_queue = []

        # Update progress tracking
        progress_panel['crawler_threads'].n = 爬取线程数
        progress_panel['crawler_threads'].total = 爬取线程数
        progress_panel['crawler_threads'].refresh()
        progress_panel['current_epoch_progress'].update(-progress_panel['current_epoch_progress'].n)
        progress_panel['current_epoch_progress'].total = len(current_queue)

        # Crawl all URLs in current queue
        with ThreadPoolExecutor(max_workers=max(1, round(爬取线程数))) as executor:
            for found_links in executor.map(crawl_single_url, current_queue):
                link_count = len(found_links)
                for url in found_links:
                    if url not in visited_urls:
                        new_queue.append((url, 1 / link_count))

        # Process any remaining documents in the index queue
        if pending_index_queue:
            batch_index_documents()

        if not new_queue:
            print('Queue is empty, stopping crawl!')
            return

        original_queue_size = len(new_queue)
        current_queue = prioritize_urls(new_queue)

        # Generate statistics and domain distribution
        domain_counter = Counter([netloc(url) for url in current_queue])

        # Reset domain distribution histogram
        for bucket in domain_distribution_histogram._buckets:
            bucket.set(0)
        for count in domain_counter.values():
            domain_distribution_histogram.observe(count)

        shrunk_domain_counter = Counter([shrink_url(url) for url in current_queue])

        # Record crawl statistics
        epoch_stats = {
            'epoch': epoch,
            'discovered_urls': original_queue_size,
            'selected_urls': len(current_queue),
            'unique_domains': len(domain_counter),
            'unique_root_domains': len(shrunk_domain_counter),
            'top_domains': dict(domain_counter.most_common(20)),
            'top_root_domains': dict(shrunk_domain_counter.most_common(20)),
        }
        crawl_statistics.append(epoch_stats)

        # Save statistics to file
        with open('crawl_statistics.json', 'w', encoding='utf8') as f:
            json.dump(crawl_statistics, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    breadth_first_search(入口)
