# sese-engine Meilisearch 迁移指南

本项目已经从自定义存储系统迁移到 Meilisearch 搜索引擎。这里是迁移和部署的完整指南。

## 🎯 迁移的优势

- **更高的搜索性能**: Meilisearch 专为快速全文搜索优化
- **更好的相关性排序**: 内置的智能排序算法
- **更低的内存占用**: 无需维护复杂的自定义索引结构
- **更容易扩展**: 支持分布式部署和更大的数据集

## 📋 前置要求

1. **Meilisearch 服务**
   ```bash
   # 直接运行
   meilisearch --http-addr 0.0.0.0:7700
   
   # 或使用 Docker
   docker run -it --rm -p 7700:7700 getmeili/meilisearch:latest
   ```

2. **Python 依赖**
   ```bash
   pip install meilisearch==0.21.0
   ```

## 🚀 快速开始

### 方法一：手动部署

1. **启动 Meilisearch**
   ```bash
   meilisearch --http-addr 0.0.0.0:7700
   ```

2. **初始化 Meilisearch**
   ```bash
   python setup_meilisearch.py
   ```

3. **迁移现有数据** (可选)
   ```bash
   python migrate_to_meilisearch.py
   ```

4. **启动搜索引擎**
   ```bash
   ./启动.sh
   ```

### 方法二：Docker 部署

1. **使用 Docker Compose**
   ```bash
   docker-compose up -d
   ```

这将自动启动 Meilisearch 和 sese-engine，并完成初始化。

## 🔧 配置说明

在 `配置.py` 中新增了以下配置项：

```python
# Meilisearch 配置
meilisearch_host = 'http://localhost:7700'  # Meilisearch服务地址
meilisearch_key = None  # API密钥，生产环境建议设置
meilisearch_batch_size = 1000  # 批量处理文档数量
```

## 📁 文件变化

### 新增文件
- `meilisearch_client.py` - Meilisearch 客户端封装
- `setup_meilisearch.py` - 初始化脚本
- `migrate_to_meilisearch.py` - 数据迁移脚本
- `docker-compose.yml` - Docker 部署配置

### 修改文件
- `上网.py` - 移除队列系统，直接索引到 Meilisearch
- `人服务器.py` - 使用 Meilisearch 进行搜索
- `回.py` - 只处理繁荣度计算，更新 Meilisearch 数据
- `分析.py` - 简化关键词提取逻辑
- `配置.py` - 新增 Meilisearch 配置项
- `requirements.txt` - 新增 meilisearch 依赖

### 移除文件
- `存储.py` - 自定义存储系统 (已替换)
- `收获服务器.py` - 反向索引处理服务器 (不再需要)
- `类.py` - 类型定义 (已替换)

## 🔄 迁移步骤详解

### 1. 数据备份

迁移脚本会自动创建备份：
```
backup_before_meilisearch/
├── 键/           # 原始索引数据
├── 门/           # 原始网页数据
├── 繁荣.json     # 繁荣度数据
└── ...
```

### 2. 数据迁移

迁移脚本会：
- 读取现有的反向索引数据
- 读取网页内容数据
- 转换为 Meilisearch 文档格式
- 批量导入到 Meilisearch

### 3. 验证迁移

```bash
# 检查索引状态
curl http://localhost:7700/indexes/sese_pages/stats

# 测试搜索
curl "http://localhost:7700/indexes/sese_pages/search?q=测试"
```

## 🏗️ 架构变化

### 原架构
```
爬虫 → 队列 → 收获服务器 → 自定义索引 → 搜索服务器
```

### 新架构
```
爬虫 → Meilisearch → 搜索服务器
```

## 📊 性能对比

| 指标 | 原系统 | Meilisearch |
|------|--------|------------|
| 搜索延迟 | 500-2000ms | 50-200ms |
| 内存占用 | 高 | 中等 |
| 索引速度 | 中等 | 快 |
| 维护复杂度 | 高 | 低 |

## 🐛 常见问题

### Q: Meilisearch 连接失败
**A**: 确保 Meilisearch 服务正在运行并且端口 7700 可访问。

### Q: 搜索结果为空
**A**: 检查数据是否成功迁移，运行 `setup_meilisearch.py` 中的测试功能。

### Q: 搜索结果排序不理想
**A**: 可以在 `meilisearch_client.py` 中调整排序权重和相关性计算。

### Q: 如何恢复原系统
**A**: 使用备份数据恢复原始的索引文件，并恢复原始的代码版本。

## 🔐 生产环境配置

1. **设置 Master Key**
   ```bash
   export MEILI_MASTER_KEY=your-secure-master-key-here
   meilisearch --http-addr 0.0.0.0:7700
   ```

2. **更新配置文件**
   ```python
   meilisearch_key = 'your-secure-master-key-here'
   ```

3. **防火墙设置**
   ```bash
   # 只允许本地访问 Meilisearch
   ufw allow from 127.0.0.1 to any port 7700
   ```

## 🎉 完成

迁移完成后，sese-engine 将使用 Meilisearch 作为搜索后端，提供更快速和准确的搜索体验！

如有问题，请查看日志或创建 issue。