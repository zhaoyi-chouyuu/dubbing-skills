# TEST status · v0.8

This skill is an experimental workflow and is expected to change as it is used on real video projects.

It has not yet passed a complete end-to-end media run using the current Turbo + Anime Whisper review + V26 alignment + final editorial review workflow. It has not been validated for stable processing of thousands of minutes. The repository version documents the current workflow; it is not a packaged one-click transcription system.

Current intended behavior: Whisper large-v3-turbo produces the Japanese draft; Anime Whisper checks text only and does not set subtitle boundaries or timing; V26 determines timing; speaker-voiceprint stages are opt-in and disabled by default; a full editorial review happens before delivery. Future changes should be recorded after practical use.
