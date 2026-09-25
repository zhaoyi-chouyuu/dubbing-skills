# beta1.6 voice-anchor handoff

Preparation supports one beta1.6 workflow. It first creates episode-local anonymous `SpeakerNN` turns and a no-name script draft, plus the episode-1 review evidence and a blank voice-enrollment template. It does not derive named voice anchors before manual confirmation.

After the script workflow completes episode 1, the user checks the full character-to-dialogue mapping. Only user-confirmed named-role intervals may then enter the voice gallery, and only after explicit extraction authorization.

Use the full operating rules in:

`C:\Users\zhao-\.claude\skills\dubbing-script-automation\references\beta-voice-anchor-workflow.md`

The handoff must preserve the first-episode source hashes and use these internal markers:

```text
workflow_stage=beta
workflow_version=beta1.6
voice_evidence_authority=supporting
automatic_identity_assignment=false
```

Rejected or insufficient roles remain `no_reliable_gallery`. A visible face, TalkNet result, anonymous cluster, or nearest voice cannot establish an enrollment identity. The downstream ensemble uses CAM++, ECAPA512, and ResNet34; WavLM is disabled. Never score an episode with anchors extracted from that same episode.
