# Coarse audio fallback

Use this fallback only when the confirmed three-model voice ensemble plus the independent dialogue check cannot safely identify the speaker, or when their evidence conflicts. It is a listening aid, not speaker identification.

## Procedure

1. Open the complete semantic-unit interval in the original episode video. Listen to enough surrounding audio to hear the turn transition; do not judge an isolated subtitle fragment when the voice begins earlier or continues later.
2. Classify only broad audible traits: `male_voice`, `female_voice`, `older_voice`, `younger_voice`, `mixed_voice`, or `unclear_voice`. Use `older_voice` or `younger_voice` only when the age impression is reasonably clear; otherwise use the gender-only class or `unclear_voice`.
3. Note overlap, music, effects, crying, whispering, dubbing artifacts, and other contamination. Use `mixed_voice` or `unclear_voice` instead of forcing a class.
4. Compare the coarse class with the current candidate role's approved gender/age metadata. Use it to narrow candidates or flag a conflict, never to invent a named role or override clear visual/semantic evidence.
5. Record `coarse_audio_status=reviewed`, `audio_interval` in `HH:MM:SS,mmm --> HH:MM:SS,mmm` form, one `audible_gender_age` class, `audio_reviewed_by`, and `audio_conflict=true|false`. If it changes or supports the final decision, explain that in `conflict_resolution`. Keep the original visual and semantic evidence intact.

## Decision rules

- A coarse audio class can support a role already supported by visual/semantic evidence.
- A coarse audio conflict routes the row back to continuous-video and semantic review; it does not automatically change `final_role`.
- Do not call this `voice verification`, `voiceprint matching`, or `acoustic identity confirmation`.
- Do not build or require a voice gallery for this fallback.
- If the audio cannot be heard reliably, record `unclear_voice`. Do not skip the listening record and do not convert it into a named-role identity claim.

## Production gate

Coarse source listening is mandatory only when clean-voice matching is unavailable, ineligible, ambiguous, low-similarity, short, mixed, or conflicting, or when video context is required for reaction-shot/off-screen/cutaway continuity. Missing or malformed audio fields then block `apply-review` and episode QC. An eligible, non-conflicting clean-voice match may resolve the acoustic stage without opening the original video.
