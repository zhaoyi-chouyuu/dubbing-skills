# 极短夹带残留：共用检查入口

共用实现与完整规则在 `C:/Users/zhao-/.agents/skills/subtitle-align-skill-gpu/references/short-transition-review.md`；使用前读取该文件。检测器是同目录的 `check_short_transitions.py`，不复制另一份算法。

Windows：`python C:/Users/zhao-/.agents/skills/subtitle-align-skill-gpu/check_short_transitions.py <字幕.srt> --output <检查.json>`。
WSL：`~/miniconda/envs/stable-ai/bin/python /mnt/c/Users/zhao-/.agents/skills/subtitle-align-skill-gpu/check_short_transitions.py <字幕.srt> --output <检查.json>`。

文字确认后/对齐前及合并后的最终版各跑一次。参考时间不可信时按顺序、人物和上下文建立映射，在 `--protected` 清单记录确认的真实重复/叠说/画面字及来源；不按同 ID 或同时间自动配对。纯短句、长音、语气词不能因短就删除。保护清单不许可更改字幕，也不等同听审完成。

纯对齐只列候选，不能改字、删段、变换行。已授权文字返修时，只有参考或实际音频能证明没有独立发话的桥接残留才可删除；保留原段、理由、源位置和 ID 映射，确认邻段文字/时码及画面字无非授权改变，复扫新接缝。双人同文、真实重复绕开 V26 Phase 0 的无条件去重；路径未验证时保留并标 needs_review。
