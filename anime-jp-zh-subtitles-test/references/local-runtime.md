# Windows / WSL 本机运行说明

记录日期：2026-10-28。下面是发现时的状态，执行前重新检查；本次只保存技能，没有双路媒体试跑。

## 默认运行后端

用户已明确选择 WSL 优先。实际媒体阶段默认使用 Ubuntu 内部的 Python 环境、模型和缓存，输入/成品按用户指定位置读写；Windows 侧可做文件管理和静态预检。Windows 后备只在 WSL 存在具体阻碍时评估，并在运行记录中说明平台变化；不因短样本性能排名自动改变此偏好。此选择尚不构成长批稳定性测试结论。

## 已有资源

- WSL2 Ubuntu 的 Linux 文件系统由 `E:\WSL\Ubuntu\ext4.vhdx` 保存；不要把 `/mnt/e/...` 的 NTFS 文件夹当作 Linux 原生目录。
- ASR-A 主路：`/home/zhaoyi/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`，期望仓库 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`。对应 Windows 缓存根 `E:\AI_Models\huggingface\hub`；其中部分快照为 WSL 符号链接，Windows 可见目录不代表可读权重。2026-10-28 在 WSL 再次核对 refs/main 与快照文件，未重跑媒体。
- ASR-B 仅文本审查：`/home/zhaoyi/models/anime-whisper`；对应 Windows 原件 `E:\AI_Models\anime-whisper`。期望仓库 `litagin/anime-whisper`，revision `22e2008a8182b357da3922a6308d095008f72973`；已知本机权重 SHA256 `15c672f0bf687b1c67aa14325f9c382c6919f1fce976f990513618b464a3c626`。执行时核对实际文件，不仅查缓存记录。
- WSL 原生 Python：`/home/zhaoyi/miniconda/envs/stable-ai/bin/python`，入口 `/home/zhaoyi/.local/bin/asr-python`。当前已知 torch 2.8.0+cu128、Transformers 4.51.3、WhisperX 3.8.5、CTranslate2 4.7.1；这是发现记录，不是其他环境的锁定版本。
- 缓存：`/home/zhaoyi/.cache`；运行材料建议 `/home/zhaoyi/.cache/anime-subtitle-test/`。用户指定同目录成品时，完成后复制成品到指定 Windows 路径，原始记录留在运行目录。不得把已有错误 TXT/SRT 当可信参考答案。
- 原生 Hugging Face 缓存内有日语 `jonatasgrosman/wav2vec2-large-xlsr-53-japanese`；实际快照、处理器配置、可读权重与模型接口在运行时检查。
- 当前组合不需要 Qwen3-ASR 或 `qwen-asr` 包；之前未发现其权重，不因此阻塞 Turbo + Anime Whisper。其他体系仅作为后续可选复核，不自动下载。声纹默认关闭，不检查/加载其资源；仅明确启用时核对实际选用的模型和样本。音频复核工具和联合对齐适配器仍须检查，不能从存在旧流程推断已通过验证。

可用任意已有 Python 运行 `scripts/preflight.py`，显式传 `--turbo-model` 与 `--anime-model`。不传 Turbo 路径时从本平台的本机缓存 refs/main 查找上述仓库快照，不联网补齐。它仅检查本地文件和当前解释器包元数据，不载入模型、不读取视频；CTranslate2 model.bin 只做非空和首尾可读检查，不解析其全部张量。blocked_dependency 不妨碍保存技能，preflight_only 也不能宣称双路试跑成功。

## 两路接口

ASR-A 使用 `faster_whisper.WhisperModel(local_snapshot, device="cuda", compute_type="float16", local_files_only=True)`；实际安装版本签名优先。`.transcribe(audio, language="ja", task="transcribe", word_timestamps=True, condition_on_previous_text=False, vad_filter=False)` 可作为完整原音首轮起点。beam/温度等显式记录，返回的 segments 生成器必须遍历。使用 batched 接口时注意其 VAD 默认行为，不能让两路都丢弃同一套 VAD 阴性区间。词级时间作为粗定位材料，最终字幕时间由 V26 确定。

ASR-B 使用 Transformers 的 `AutoProcessor`、`WhisperForConditionalGeneration`，真实加载 Anime Whisper 的本地 `model.safetensors` 并指定设备和 dtype。按模型上限切片、保留上下文重叠，不传 initial_prompt/前文答案。首轮禁止覆盖；重复抑制仅作为记录参数的额外复核试验。记录生成 token 数/上限，触及上限作为可能截断的疑点。该路只输出内容审查候选；其省略句号的文本和识别窗口均不得参与字幕断句或 timing。

两路同属 Whisper 系，独立听写不代表错误独立。保留全音轨查漏，分歧按声音证据处理，不自动以主路或多数票取胜。RTX 4070 Laptop 8 GB 上先小批量、分阶段加载，记录实际显存；同一模型处理一批时加载一次并复用，不每条音频重新加载。最终 timing 读取 `subtitle-align-skill-gpu`，使用 V26 和本技能的 [适配约束](alignment-adapter.md)，不使用 Anime Whisper 的边界。

仅创建/修改技能不授权下载、安装、云端上传或媒体试跑。未来发现依赖缺失时说明模型、用途和需要的操作，再根据当次任务授权处理。

## 官方资料

- Anime Whisper 模型卡：https://huggingface.co/litagin/anime-whisper
- Faster Whisper 官方代码与分段/词级时间接口：https://github.com/SYSTRAN/faster-whisper

官方接口已在本次写作时核对；模型加载、真实双路听写、声纹边界融合、翻译和长批稳定性不属于本次验证范围。
