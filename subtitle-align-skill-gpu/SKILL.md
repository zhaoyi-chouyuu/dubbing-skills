---
name: subtitle-align-skill-gpu
description: Align Japanese subtitle timecodes to dubbing audio with the V26 GPU workflow, preserving subtitle text and producing per-episode SRT and QC reports.
metadata:
  source: OpenClaw subtitle-align-skill(GPU), adapted for Claude Code
  version: V26
---

# 日语字幕时间码调整 Skill (subtitle-align-skill) — V26 ⭐ 默认版本

## ⚠️ 作业规则（重要）

**素材位置（全部在 E 盘）：**
- `E:\短剧配音字幕对齐\` — 字幕对齐项目主目录
  - `vocal/<剧名>/` — 原始音频（按剧名建立子文件夹）
  - `vocal_merged/<剧名>/` — 合并后的音频
  - `subtitle(no alignment)/<剧名>/` — 未对齐的字幕（SRT）
  - `output/<剧名>/` — 对齐好的字幕输出
  - `output/<剧名>/qc_reports/` — 质检报告（QC/Auto-Fix，不与字幕混放）
- `E:\短剧配音echo消除\` — 去回声处理
- `E:\短剧配音人声分离\` — 人声分离处理

**⚠️ 文件夹整理规则（重要）：**
多个项目共存时，必须按剧名建立子文件夹，结构示例：
```
E:\短剧配音字幕对齐\
  ├─ vocal/
  │   ├─ loveagain/
  │   └─ 医路芳华/
  ├─ subtitle(no alignment)/
  │   ├─ loveagain/
  │   └─ 医路芳华/
  └─ output/
      ├─ loveagain/
      │   └─ qc_reports/
      └─ 医路芳华/
          └─ qc_reports/
```
这样做可以直观看出哪些项目完成了哪些没完成。

**工作流程（Claude Code）：**
1. 直接开始处理，**不提问**
2. 用 PowerShell 工具通过 `wsl -e bash -lc "..."` 在 WSL 里运行 V26 脚本。整批对齐属于长任务，必须用 `run_in_background: true` 启动，完成时会自动通知；不要用 sleep 循环轮询。需要中途看进度时，读输出目录里的 `.progress_v26.json`。
3. 完成后核对逐集输出、QC 报告和压缩包，再向用户返回状态与路径。

**对齐脚本：**
- 主引擎：本 skill 目录下的 `master_aligner_v26.py`
  - Windows 路径：`C:\Users\zhao-\.claude\skills\subtitle-align-skill-gpu\master_aligner_v26.py`
  - WSL 路径：`/mnt/c/Users/zhao-/.claude/skills/subtitle-align-skill-gpu/master_aligner_v26.py`
  - 不要使用 `/home/zhaoyi/.openclaw/workspace/` 下的旧版引擎（旧版会合并字幕块内的重复行，改动字幕正文）。
- 剧名适配脚本：按需创建 `run_v26_<剧名>_<范围>.py`，统一放在 `E:\短剧配音字幕对齐\runners\`（WSL：`/mnt/e/短剧配音字幕对齐/runners/`）。脚本开头这样导入引擎：
  ```python
  sys.path.insert(0, "/mnt/c/Users/zhao-/.claude/skills/subtitle-align-skill-gpu")
  import master_aligner_v26 as M
  ```
  逐集调用 `M.align_one(wav, srt, out_path, vad_mode="hybrid")`，再调用 `M.quality_check_and_autofix(...)`，并写入 `.progress_v26.json`。旧的适配脚本可参考 `/home/zhaoyi/.openclaw/workspace/run_v26_*.py`，只需要替换上面这行导入路径。

**环境要求：**
- Python 环境: `/home/zhaoyi/miniconda/envs/stable-ai/bin/python`（WSL）
- XDG_CACHE_HOME: `/mnt/e/AI_Models`

**运行示例（PowerShell 工具，后台运行）：**
```powershell
wsl -e bash -lc "export XDG_CACHE_HOME=/mnt/e/AI_Models; cd /mnt/e/短剧配音字幕对齐/runners && /home/zhaoyi/miniconda/envs/stable-ai/bin/python run_v26_<剧名>_<范围>.py"
```

**输出文件名：** `片名_集数_speedy_V26.srt`
**输出路径：** `E:\短剧配音字幕对齐\output\<剧名>/`

**Shell 说明：** 本文件中的 `bash` 代码块（ffmpeg、curl、gdown、Python 片段等）都要在 WSL 里运行，写法是 `wsl -e bash -lc "..."`，路径用 `/mnt/e/...`。不要直接在 PowerShell 里运行。

---

## ⚠️ 多轨音频合并（台词 + 独白）

有些素材的 vocal 文件会分成两条音轨：
- `セリフ`（台词）
- `モノローグ`（独白/旁白）

**需要先合并成一条音轨再进行字幕对齐。**

### 识别方法
```bash
ls "vocal/"  # 查看文件列表
# 有两条的示例：#1_セリフ.wav, #1_モノローグ.wav
# 只有一条的示例：#4_セリフ.wav
```

### 合并命令（FFmpeg amix）
```bash
VOCAL_DIR="vocal"
MERGED_DIR="vocal_merged"
mkdir -p "$MERGED_DIR"

# 合并有两条音轨的
for num in 1 2 3 5 6 8; do
    ffmpeg -y -i "${VOCAL_DIR}/#${num}_セリフ.wav" \
           -i "${VOCAL_DIR}/#${num}_モノローグ.wav" \
           -filter_complex "amix=inputs=2:duration=longest" \
           -ar 16000 -ac 1 "${MERGED_DIR}/${num}_merged.wav"
    echo "完成: #${num}"
done

# 复制只有一条音轨的
for num in 4 7 9 10; do
    cp "${VOCAL_DIR}/#${num}_セリフ.wav" "${MERGED_DIR}/${num}_merged.wav"
done
```

### 关键参数
- `amix=inputs=2:duration=longest` — 合并两条音轨，时长取较长的
- `-ar 16000 -ac 1` — 转成 16kHz 单声道（Whisper 推荐格式）

---

## ⚠️ Gigafile 下载素材

```bash
# 1. 获取 Session Cookie（直接访问目标 URL 自动获取）
curl -c /tmp/giga_cookies.txt -L -s -A "Mozilla/5.0" "https://44.gigafile.nu/<文件ID>" > /dev/null

# 2. 下载（-C - 断点续传 -L 跟随重定向）
curl -b /tmp/giga_cookies.txt -C - -L \
  "https://44.gigafile.nu/download.php?file=<文件ID>" \
  -o 保存路径/文件名.zip
```
- `-C -`：自动断点续传，断了也不怕
- cookies 文件用 `/tmp/` 即可
- 文件ID从URL中提取（例如 `0421-nbd10a7a9f0726df5a5326e5a0748cbc6`）
- **不需要手动提供 cookie**，直接访问目标 URL 即可自动获取

---

## ⚠️ Google Drive 下载素材

### 自动化下载脚本（推荐）

```bash
wsl -e bash -lc "bash /mnt/c/Users/zhao-/.claude/skills/subtitle-align-skill-gpu/gd_download.sh '<Google Drive 文件夹链接>'"
```

脚本功能：
- 🔍 自动扫描文件夹，提取所有 SRT 和 WAV 文件
- ⏭️ 跳过 E 盘已有的文件，只下载新的
- 📁 自动识别剧名，归位到 `E:\短剧配音字幕对齐\vocal/<剧名>/` 和 `subtitle(no alignment)/<剧名>/`
- 🚀 SRT 小文件用 gdown，WAV 大文件用 curl（不限流）
- 📝 SRT 自动重命名为数字格式（如 `11.srt`）

### 手动单独下载

```bash
# SRT 小文件 — gdown
gdown "https://drive.google.com/uc?id=<文件ID>" -O 保存路径/

# WAV 大文件 — curl（gdown 会限流）
FILEID="<文件ID>"
curl -sc /tmp/gd_cookie.txt -L "https://drive.google.com/uc?export=download&id=${FILEID}" -o /dev/null
CONFIRM=$(awk '/download_warning/ {getline; print $NF}' /tmp/gd_cookie.txt)
curl -Lb /tmp/gd_cookie.txt \
  "https://drive.google.com/uc?export=download&confirm=${CONFIRM}&id=${FILEID}" \
  -o 保存路径/TCT_XX.wav
```

### 文件 ID 提取方法

```bash
# 用 gdown 扫描文件夹（timeout 避免实际下载）
timeout 15 gdown --folder "<Google Drive 链接>" 2>&1 | grep -oP 'Processing file [\w\-]+ .*'
# 输出: Processing file <FILE_ID> <文件名>
```

---

## 📝 输出文件名规范

格式：`片名_集数_speedy_V26`
示例：`狂少出山_38_speedy_V26.srt`

- 片名：从原字幕文件名中提取（如 `狂少出山`）
- 集数：两位数字（如 01、02）
- 校对人：**speedy**
- 算法版本：**V26**（Chunked Local Alignment + Triple VAD）

**重要：必须保持原名不改变片名和集数格式**

---

## 功能
将日语字幕的时间码与音频进行手术级对齐，保持原字幕行数和文本不变。

## 环境要求
- Python 环境: `~/miniconda/envs/stable-ai/`
- NumPy >= 2.1 (WhisperX 需要)
- WhisperX == 3.8.5
- pysrt
- GPU: NVIDIA RTX 4070 (8GB VRAM)
- V26 引擎：`master_aligner_v26.py`（从 D盘 `AI_Skills/06_Subtitle_Alignment/` 同步）

## 模型位置
| 模型 | 路径 |
|------|------|
| Whisper large-v3-turbo | E:\AI_Models\Whisper\large-v3-turbo.pt |
| Whisper medium | E:\AI_Models\Whisper\medium.pt |
| Wav2Vec2 Japanese | E:\AI_Models\huggingface\hub\models--jonatasgrosman--wav2vec2-large-xlsr-53-japanese |

## 环境变量
```bash
export XDG_CACHE_HOME=/mnt/e/AI_Models
source ~/miniconda/bin/activate stable-ai
```

## 核心参数
- OFFSET_START = 0.15 (进入点延迟，避免字幕略早)
- OFFSET_END = 0.000 (已去除，由 Librosa 孤岛物理边界决定)
- 最小字幕时长: 0.5秒
- 重叠间隔: 50ms
- **推荐模型: large-v3-turbo** (速度快，精度高)

## 🚀 V26 Chunked Local Alignment 算法（默认）

**核心逻辑："Triple-Engine Hybrid VAD + 分段对齐防漂移 + 幻觉熔断 + 智能质检"**

1. **标点符号过滤 (Punctuation-Free Density)**：判定语速密度时自动剔除所有标点，纯靠文字长度运算，杜绝标点导致的误判。
2. **长音频分段对齐防漂移 (Chunked Local Alignment)**：根据停顿点（>0.5s）自动将长音频切分为安全区块（Super Segments），每段独立对齐，消除 WhisperX 累计误差。
3. **Triple-Engine Hybrid VAD**：Silero AI 语义岛 + Librosa 物理波形岛双重校验，精准锁定语音边界。
4. **WhisperX 尾音幻觉熔断 (Hallucination Guard)**：检测单字超过 0.6s 的 AI 异常拉伸，自动截断。
5. **Librosa 防过度切除兜底 (Over-truncation Guard)**：物理边界过度切除时自动恢复 WhisperX 语义边界，防止吞字。
6. **安全字幕时长比例保护**：字数多但时长极短时数学平滑拉伸，杜绝跨音频岛跳转。
7. **增强 QC 质检 + Auto-Fix**：Cross-check 标记 HIGH_DENSITY/LOW_DENSITY/SILENT/WHISPER_EMPTY 等可疑点，自动修复静音空行，生成 `*_QC_REPORT.txt` + `*_AUTO_FIX_LOG.txt`。
8. **进度追踪**：`.progress_v26.json` 三态记录（passed/needs_review/failed），支持断点续跑。

### V26 核心引擎

V26 主引擎脚本位于本 skill 目录：`master_aligner_v26.py`

剧名适配脚本示例：`run_v26_<剧名>_<范围>.py`，负责：
- 递归搜索剧名子目录下的音频文件
- 匹配对应的 SRT 文件
- 调用 V26 引擎对齐
- 输出到 `<剧名>` 专用输出子目录

### V26 旧版参考代码（已弃用）
```python
import whisperx, pysrt, torch, librosa, os

def fmt(seconds):
    if seconds < 0: seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h:02d}:{m:02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}'

device = 'cuda' if torch.cuda.is_available() else 'cpu'

# 1. 加载音频 + Librosa 能量分析
y, sr = librosa.load(audio_path, sr=16000)
duration_total = len(y) / sr
islets = librosa.effects.split(y, top_db=25)  # 语音孤岛
islet_times = [(start/sr, end/sr) for start, end in islets]

# 2. 加载 SRT
try:
    subs_orig = pysrt.open(srt_path, encoding='utf-8-sig')
except:
    subs_orig = pysrt.open(srt_path, encoding='utf-8')

# 3. WhisperX 语义锚定
audio = whisperx.load_audio(audio_path)
model_a, metadata = whisperx.load_align_model(language_code='ja', device=device)
full_text = ''.join([s.text.replace('\n', '').replace(' ', '').strip() for s in subs_orig])
full_transcript_segment = [{'text': full_text, 'start': 0, 'end': duration_total}]
aligned = whisperx.align(full_transcript_segment, model_a, metadata, audio, device, return_char_alignments=True)

# 4. 提取字符级时间线
char_timeline = []
for seg in aligned.get('segments', []):
    if 'chars' in seg:
        for c in seg['chars']:
            if 'char' in c and 'start' in c and 'end' in c:
                char_timeline.append(c)

# 5. 逐条字幕对齐 + 物理质检 + 促音保护
results = []
current_pos = 0
MIN_GAP = 0.080  # 最小间隙80ms

for sub in subs_orig:
    clean_text = sub.text.replace('\n', '').replace(' ', '').strip()
    row_chars = char_timeline[current_pos : current_pos + len(clean_text)]
    current_pos += len(clean_text)
    
    if row_chars:
        s_time = row_chars[0]['start']
        ai_e_time = row_chars[-1]['end']
        e_time = ai_e_time
        
        # 找到匹配的语音孤岛
        matched_islet_idx = -1
        for i, (i_s, i_e) in enumerate(islet_times):
            if i_s <= s_time + 0.1 <= i_e:
                e_time = i_e
                matched_islet_idx = i
                break
        
        # 促音保护：检测促音（っ/ッ），300ms内有后续音节则桥接
        has_sokuon = 'っ' in clean_text or 'ッ' in clean_text
        if has_sokuon and matched_islet_idx != -1 and matched_islet_idx < len(islet_times) - 1:
            next_s, next_e = islet_times[matched_islet_idx + 1]
            if (next_s - e_time) < 0.300:
                e_time = next_e
        
        # 长句保护：如果时间太短，延伸到下一个孤岛
        if len(clean_text) > 5 and (e_time - s_time) < (len(clean_text) * 0.06):
            if matched_islet_idx != -1 and matched_islet_idx < len(islet_times) - 1:
                e_time = islet_times[matched_islet_idx + 1][1]
        
        e_time = min(ai_e_time + 0.1, e_time + 0.05)
        if e_time < s_time + 0.3:
            e_time = s_time + 0.6
    else:
        s_time = (results[-1][1] + 0.1) if results else 0.5
        e_time = s_time + 1.5
    
    results.append([s_time, e_time])

# 6. 修复重叠
for i in range(len(results) - 1):
    if results[i][1] > results[i+1][0] - MIN_GAP:
        results[i][1] = results[i+1][0] - MIN_GAP

# 7. 保存
with open(output_path, 'w', encoding='utf-8-sig') as f:
    for idx, (sub, (s, e)) in enumerate(zip(subs_orig, results), 1):
        f.write(f'{idx}\n{fmt(s)} --> {fmt(e)}\n{sub.text}\n\n')
```

### V26 vs V22.2 对比
| 特性 | V22.2 | V26 |
|------|-------|-----|
| VAD 引擎 | Librosa 单引擎 | Silero AI + Librosa 双引擎 |
| 长音频 | 整段对齐（可能漂移） | 分段对齐防漂移 |
| 标点处理 | 无 | 自动剔除，纯文字密度 |
| 尾音幻觉 | 无防护 | 0.6s 自动熔断 |
| 过度切除 | 无防护 | 自动恢复语义边界 |
| QC 质检 | 基本标记 | 密度分析 + Auto-Fix |
| 进度追踪 | 无 | .progress_v26.json |
| 精度 | ~10ms | ~5ms |

**默认使用 V26 算法**

## ⚡ 日语快语速优化参数
对齐时使用以下参数，对日语快语速场景效果更好：
```python
aligned = whisperx.align(
    transcript,
    model_a,
    metadata,
    audio,
    device,
    interpolate_method='nearest',   # 默认值，恢复
    return_char_alignments=True     # 字符级对齐，日语更适合
)

```
- `interpolate_method='linear'`：快语速时比nearest更平滑
- `return_char_alignments=True`：日语是字符语言，字符级比词级更精细

## 标准流程

> ⚠️ **强制规则**：Phase 0 必须在所有操作之前执行，不能跳过！

### Phase 0: 结构损坏扫描与修复

**在进行任何对齐之前，必须先检查 SRT 文件的完整性。**

#### 故障描述
- **症状**：序号（Index）和时间轴（Timestamp）紧贴在上一个 Subtitle Block 的正文下方，中间缺少空行分隔。
- **原因**：SRT 文件结构被破坏，通常是手动编辑或非专业工具造成的。
- **后果**：pysrt 解析后会串行或跳行，导致字幕顺序错乱。

#### 修复方案
```python
import re

def scan_and_fix_srt_structure(srt_path):
    """
    扫描 SRT 文件的结构损坏，并在原位修复。
    匹配模式：字幕正文末尾 -> 下一个字幕的序号（中间无空行）
    """
    with open(srt_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    # 检测模式：字幕正文（非空行）直接跟着序号行，中间没有空行分隔
    # 匹配：任意非空行的文本 \n 紧跟一个数字序号（后面是时间轴）
    # 正则：(\d\n\d{2}:\d{2}:\d{2},\d{3} --> )  
    # 但更精确的是匹配「字幕文本行」+「下一个序号行」之间缺空行的情况
    
    # 查找所有序号行（纯数字行）
    lines = content.split('\n')
    fixes = []
    
    for i, line in enumerate(lines):
        line = line.strip()
        # 检查是否是序号行（单独一个数字）
        if re.match(r'^\d+$', line):
            # 检查前一行是否为空行
            if i > 0 and lines[i-1].strip() != '':
                prev_line = lines[i-1].strip()
                # 前一行不是空行，说明缺少分隔
                fixes.append(f"Line {i+1}: 序号前缺少空行，前一行='{prev_line[:30]}'")
    
    if fixes:
        print(f"⚠️ 发现 {len(fixes)} 处结构损坏：")
        for fix in fixes:
            print(f"  - {fix}")
        
        # 修复：在序号行前插入空行
        # 匹配：非空行文本 + 换行 + 序号 + 换行 + 时间轴
        fixed_content = re.sub(r'(\S[^\n]*)(\n)(\d{1,4})(\n\d{2}:\d{2}:\d{2},\d{3} -->)', 
                               r'\1\n\n\3\4', content)
        
        with open(srt_path, 'w', encoding='utf-8-sig') as f:
            f.write(fixed_content)
        print("✅ 结构损坏已修复")
        return True
    else:
        print("✅ SRT 结构完好，无损坏")
        return False

# 使用
scan_and_fix_srt_structure(srt_path)
```

#### 强制原则
- ✅ **只调整格式**（空行），严禁修改任何字幕正文内容
- ✅ 先扫描确认有损坏再修复
- ✅ 修复前可备份原文件
- ⚠️ 如果修复后仍有问题，可能是其他编码或格式问题

#### 检测模式说明
```
# 正常结构（Subtitle Block 之间有空行分隔）：
1
00:00:01,000 --> 00:00:03,500
字幕正文第一行

2
00:00:04,000 --> 00:00:06,500
字幕正文第二行

# 损坏结构（序号和时间轴紧贴在上一个字幕正文下方）：
1
00:00:01,000 --> 00:00:03,500
字幕正文第一行
2              <-- 这里缺少一个空行
00:00:04,000 --> 00:00:06,500  <-- 这里也紧贴上面
字幕正文第二行
```

---

### 1. 预处理 SRT 文件
有些 SRT 文件包含 `\r` 回车符，会导致 pysrt 解析错误。预处理：
```python
import re

with open(srt_path, 'rb') as f:
    content = f.read()

# 修复 \r 在日文字符前的问题
content = re.sub(b'\r\n([\xe3-\xe9])', b'\n\\1', content)
content = content.replace(b'\r', b'')

# 保存修复后的文件
with open(fixed_srt_path, 'wb') as f:
    f.write(content)
```

### 2. WhisperX 对齐（使用 faster-whisper）
```python
import whisperx
import pysrt
import torch
import gc
from faster_whisper import WhisperModel  # ✅ 用 faster-whisper

gc.collect()
torch.cuda.empty_cache()

def fmt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"

# 加载 SRT
subs_orig = pysrt.open(srt_path, encoding='utf-8-sig')

# 加载 Whisper 模型 (推荐 large-v3-turbo) — 用 faster-whisper ✅
model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")

# 加载音频并转录
audio = whisperx.load_audio(audio_path)
result = model.transcribe(audio, language="ja", word_timestamps=True)

# 加载对齐模型
model_a, metadata = whisperx.load_align_model(language_code="ja", device="cuda")

# 创建 SRT 模板
transcript = []
for s in subs_orig:
    text = s.text.replace("\n", " ").strip()
    if not text:
        text = "..."
    transcript.append({
        "text": text,
        "start": s.start.ordinal / 1000.0,
        "end": s.end.ordinal / 1000.0
    })

# 对齐（日语快语速优化参数）
aligned = whisperx.align(
    transcript,
    model_a,
    metadata,
    audio,
    device,
    interpolate_method='nearest',
    return_char_alignments=True
)
aligned_segs = aligned.get("segments", [])

# 获取字符级对齐（return_char_alignments=True 时可用）
char_aligned = aligned.get("character_aligned_segments", [])
```

### 3. 保存并检查
```python
OFFSET_END = 0.0  # 由 Librosa 孤岛物理边界决定，不再额外补偿

with open(output_path, "w", encoding="utf-8-sig") as f:
    prev_end = 0
    for idx, (orig_sub, aligned_seg) in enumerate(zip(subs_orig, aligned_segs), 1):
        start = aligned_seg.get("start", orig_sub.start.ordinal / 1000.0)
        is_last = (idx == len(subs_orig))
        end = aligned_seg.get("end", orig_sub.end.ordinal / 1000.0)
        # 不再加 OFFSET_END，结束时间由 Librosa 孤岛决定
        
        # 防止重叠
        if start < prev_end:
            start = prev_end + 0.01
        
        prev_end = end
        f.write(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{orig_sub.text}\n\n")
```

### 4. 自动检查与修复
```python
import pysrt

subs = pysrt.open(output_path, encoding='utf-8-sig')

MIN_DURATION = 0.5  # 最小0.5秒
FIXED_DURATION = 1.0  # 太短则延长到1秒

issues = []
for i in range(len(subs)):
    entry = subs[i]
    duration = (entry.end.ordinal - entry.start.ordinal) / 1000.0
    
    # 检查时长
    if duration < MIN_DURATION:
        entry.end.ordinal = entry.start.ordinal + int(FIXED_DURATION * 1000)
        issues.append(f"Entry {i+1}: Extended to {FIXED_DURATION}s")
    
    # 检查重叠
    if i < len(subs) - 1:
        next_entry = subs[i + 1]
        if entry.end.ordinal >= next_entry.start.ordinal:
            entry.end.ordinal = next_entry.start.ordinal - 50  # 50ms间隔
            issues.append(f"Entry {i+1}: Fixed overlap")

if issues:
    for issue in issues:
        print(f"  🔧 {issue}")
    subs.save(output_path, encoding='utf-8-sig')
    print("✅ 已修复并保存")
else:
    print("✅ 无问题")
```

## 已知问题

### 1. SRT 文件包含 \r 字符
- 原因: Windows 换行符问题
- 解决: 预处理阶段移除 \r

### 2. 短字幕被压缩
- 原因: Wav2Vec2 对齐算法对短句子有时会压缩时间
- 解决: 检查脚本会自动修复过短的字幕

### 3. NumPy 版本冲突
- ReazonSpeech 需要 NumPy < 2.0
- WhisperX 需要 NumPy >= 2.1
- 解决: 先升级 NumPy (2.x) 用于字幕对齐，对齐完成后再考虑其他模型

## 快捷命令
```bash
# 激活环境
source ~/miniconda/bin/activate stable-ai

# 升级 numpy (如果需要)
pip install "numpy>=2.1"

# 检查 SRT 文件
python3 -c "import pysrt; print(pysrt.open('file.srt'))"
```

## 输出
对齐后的 SRT 文件使用 `utf-8-sig` (带 BOM) 编码，可直接用播放器加载验证。
