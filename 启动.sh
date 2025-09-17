#!/bin/sh

#export http_proxy=http://127.0.0.1:3128
#export https_proxy=http://127.0.0.1:3128
export no_proxy=localhost,127.0.0.1

TMUX_SESSION_NAME=sese_engine

# 检查Meilisearch是否运行
echo "检查Meilisearch服务..."
curl -s http://localhost:7700/health > /dev/null
if [[ $? -ne 0 ]]; then
    echo "警告: Meilisearch服务未运行，请先启动Meilisearch服务"
    echo "启动命令: meilisearch --http-addr 0.0.0.0:7700"
    echo "或者使用Docker: docker run -it --rm -p 7700:7700 getmeili/meilisearch:latest"
    read -p "是否继续启动搜索引擎? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

which tmux &> /dev/null

if [[ $? -eq 0 ]]
then
    echo "找到了 tmux，可以把屏幕分成三块！"
    tmux new -t $TMUX_SESSION_NAME -d
    tmux split-window -t $TMUX_SESSION_NAME -h
    tmux split-window -t $TMUX_SESSION_NAME -v
    tmux select-pane -t $TMUX_SESSION_NAME -L
    tmux send-keys -t $TMUX_SESSION_NAME.0 "python 人服务器.py" Enter
    tmux send-keys -t $TMUX_SESSION_NAME.1 "while true; do python 上网.py; done" Enter
    tmux send-keys -t $TMUX_SESSION_NAME.2 "while true; do python 回.py; done" Enter
    # 移除收获服务器，不再需要
    tmux attach -t $TMUX_SESSION_NAME
    exit
fi

echo "找不到 tmux 啦，我们用丑一点的界面吧！"
python 人服务器.py &
while true; do python 上网.py; sleep 1; echo '启动这个脚本的贝壳的子皮带是: '$BASHPID; done &
while true; do python 回.py; done
# 移除收获服务器启动
exit