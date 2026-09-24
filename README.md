# Claude Code 短剧配音 Skills

日语短剧配音与字幕相关的 Claude Code skills（由 Codex 版本迁移并适配 Claude Code）。

| Skill | 用途 |
|---|---|
| `dubbing-script-preparation` | 配音素材发现、准备与匿名 Speaker 草稿 |
| `dubbing-script-automation` | 配音台本自动化（beta1.5wsl），交付三份日文 Excel |
| `dubbing-vocal-assembly` | 按台本/字幕逐句剪辑并对齐配音 |
| `subtitle-align-skill-gpu` | V26 字幕时间码对齐（WhisperX GPU） |
| `shortdrama-text-standards` | 短剧字幕文本规范与质检 |
| `cms-jp-postdub-subtitle-formatting` | 配音后日文字幕格式整理 |
| `cosyvoice3-voice-cloning` | CosyVoice3 声音克隆 WebUI |
| `style-bert-vits2-tts` | Style-Bert-VITS2 语音合成 |
| `vocal-separation-ensemble` | 人声 / BGM 分离 |

## 在另一台电脑上安装

`~/.claude/skills` 目录不存在或为空时，直接克隆到该位置：

```powershell
gh repo clone zhaoyi-chouyuu/claude-dubbing-skills "$env:USERPROFILE\.claude\skills"
```

之后更新：

```powershell
git -C "$env:USERPROFILE\.claude\skills" pull
```

安装或更新后需新开一个 Claude Code 会话才会加载。

## 运行环境要求

Skill 文件可以直接同步，但脚本依赖本机环境。另一台电脑需要具备相同配置，否则需修改各 `SKILL.md` 中的路径：

- Windows 用户目录为 `C:\Users\zhao-`（路径写死在各 `SKILL.md` 中）
- `E:\AI_Models`：模型与 Windows Python 环境 `envs\windows\speaker-evidence312`
- WSL2 Ubuntu，conda 环境 `/home/zhaoyi/miniconda/envs/stable-ai`
- NVIDIA GPU（CUDA）
- 字幕对齐工作目录 `E:\短剧配音字幕对齐`
