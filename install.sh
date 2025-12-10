
#!/bin/bash

# ========================================
# 音频转录系统一键部署脚本
# ========================================

set -e  # 遇到错误立即退出

echo "=========================================="
echo "开始部署音频转录系统"
echo "=========================================="
echo "先部署宝塔面板"
echo "基于宝塔的MySQL、Python3.12.8"
echo "[gunicorn]方式启动，基于flask框架，项目端口5000"
echo "=========================================="
echo "其他必须项："
# 3. 安装FFmpeg
echo -e "${GREEN}[3/9] 安装FFmpeg...${NC}"
apt-get install -y ffmpeg ffprobe
# 4. 安装Chromium
echo -e "${GREEN}[4/9] 安装Chromium...${NC}"
apt-get install -y chromium chromium-driver
# 6. 安装中文字体
echo -e "${GREEN}[6/9] 安装中文字体...${NC}"
sudo apt install -y fonts-noto fonts-noto-cjk fonts-noto-color-emoji fonts-noto-mono
fc-cache -fv
# 8. 安装Python依赖
echo -e "${GREEN}[8/9] 安装Python依赖包...${NC}"
cat > requirements.txt <<EOF
Flask==3.0.0
pymysql==1.1.0
markdown==3.5.1
cryptography==41.0.7
EOF

pip3 install -r requirements.txt

# 9. 创建配置文件
echo -e "${GREEN}[9/9] 创建配置文件...${NC}"

# 创建数据库配置文件
cat > db_config.json <<EOF
{
  "host": "localhost",
  "port": 3306,
  "user": "audio_user",
  "password": "Audio@2024!Secure",
  "database": "voice_transcribe",
  "charset": "utf8mb4"
}
EOF

# 创建API配置文件
cat > config.json <<EOF
{
  "whisper": {
    "base_url": "https://api.gptnb.ai",
    "api_key": "YOUR_WHISPER_API_KEY",
    "models": [
      "whisper-large-v3",
      "whisper-large-v3-turbo",
      "whisper-1"
    ],
    "selected_model": "whisper-large-v3",
    "temperature": 0.2
  },
  "gpt": {
    "base_url": "https://api.gptnb.ai",
    "api_key": "YOUR_GPT_API_KEY",
    "models": [
      "claude-sonnet-4-5-20250929",
      "gemini-2.0-flash-exp",
      "chatgpt-4o-latest",
      "grok-beta"
    ],
    "selected_model": "claude-sonnet-4-5-20250929"
  }
}
EOF

# 创建用户文件
cat > users.json <<EOF
{
  "users": []
}
EOF

# 验证安装
echo -e "${GREEN}=========================================="
echo "验证安装..."
echo "==========================================${NC}"

echo -n "Python3: "
python3 --version

echo -n "pip3: "
pip3 --version

echo -n "FFmpeg: "
ffmpeg -version | head -n 1

echo -n "Chromium: "
chromium --version

echo -n "MySQL: "
mysql --version

echo -e "\n${GREEN}=========================================="
echo "部署完成！"
echo "==========================================${NC}"
echo "以下需手动配置"
echo "基于宝塔的MySQL、Python3.12.8"
echo "[gunicorn]方式启动，基于flask框架，项目端口5000"
echo "=========================================="
