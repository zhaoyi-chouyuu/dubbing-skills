#!/bin/bash
# Google Drive → E盘 字幕对齐素材下载脚本
# 用法: bash gd_download.sh "https://drive.google.com/drive/folders/<FOLDER_ID>" [剧名]
# 默认剧名从音频前缀自动识别（如 TCT_），也可手动指定

FOLDER_URL="${1:?请提供 Google Drive 文件夹链接}"
SHOW="${2:-}"

# 提取文件夹 ID
FOLDER_ID=$(echo "$FOLDER_URL" | grep -oP '[-\w]{25,}')
echo "📂 扫描文件夹: $FOLDER_ID"

# 获取文件列表（用 gdown 的 listing 功能，只扫不下载）
echo "🔍 读取文件列表..."
LISTING=$(timeout 15 gdown --folder "$FOLDER_URL" --output /tmp/gd_scan 2>&1 || true)

# 提取 Processing file 行
echo "$LISTING" | grep -oP 'Processing file [\w\-]+ .*' | while read -r _ _ ID NAME; do
  # 判断类型和集号
  if [[ "$NAME" =~ TCT_([0-9]+)\.wav ]]; then
    EP="${BASH_REMATCH[1]}"
    TYPE="wav"
  elif [[ "$NAME" =~ ^([0-9]+)\. ]]; then
    EP="${BASH_REMATCH[1]}"
    TYPE="srt"
  else
    continue
  fi
  
  # 自动识别剧名
  if [ -z "$SHOW" ] && [[ "$NAME" =~ ^(TCT)_ ]]; then
    SHOW="${BASH_REMATCH[1]}"
  fi
  
  EP_PAD=$(printf "%02d" "$EP")
  
  # 确定目标路径
  if [ "$TYPE" = "wav" ]; then
    DEST="/mnt/e/短剧配音字幕对齐/vocal/${SHOW}/${NAME}"
  else
    DEST="/mnt/e/短剧配音字幕对齐/subtitle(no alignment)/${SHOW}/${EP_PAD}.srt"
  fi
  
  # 跳过已存在的
  if [ -f "$DEST" ] && [ "$(stat -c%s "$DEST" 2>/dev/null)" -gt 100 ]; then
    echo "  ⏭️  EP${EP_PAD} ${TYPE} 已存在，跳过"
    continue
  fi
  
  # 下载
  echo "  ⬇️  下载 EP${EP_PAD} ${TYPE}..."
  mkdir -p "$(dirname "$DEST")"
  
  if [ "$TYPE" = "wav" ]; then
    # curl 大文件下载（带自动确认）
    curl -sc /tmp/gd_cookie.txt -L "https://drive.google.com/uc?export=download&id=${ID}" -o /dev/null 2>/dev/null
    CONFIRM=$(awk '/download_warning/ {getline; print $NF}' /tmp/gd_cookie.txt)
    curl -#Lb /tmp/gd_cookie.txt \
      "https://drive.google.com/uc?export=download&confirm=${CONFIRM}&id=${ID}" \
      -o "$DEST" 2>&1
    echo "     ✅ $(ls -lh "$DEST" | awk '{print $5}')"
  else
    # SRT 小文件用 gdown 更快
    gdown "https://drive.google.com/uc?id=${ID}" -q -O "$(dirname "$DEST")" 2>&1
    # 重命名为数字格式
    SRC=$(ls "$(dirname "$DEST")/${EP}."*.srt 2>/dev/null | head -1)
    [ -n "$SRC" ] && [ "$SRC" != "$DEST" ] && mv "$SRC" "$DEST"
    echo "     ✅"
  fi
  
  sleep 1  # 避免限流
done

echo ""
echo "🎉 下载完成！剧名: ${SHOW:-未知}"
echo "   vocal: $(ls /mnt/e/短剧配音字幕对齐/vocal/${SHOW}/*.wav 2>/dev/null | wc -l) 个音频"
echo "   subtitle: $(ls /mnt/e/短剧配音字幕对齐/subtitle\(no\ alignment\)/${SHOW}/*.srt 2>/dev/null | wc -l) 个字幕"
