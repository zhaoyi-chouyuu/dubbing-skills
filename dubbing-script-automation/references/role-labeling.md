# Role-labeling quality standard

## Catalog authority

Use the reconciled approved catalog from preparation. Build it from the union of the cast table, embedded images, user screenshot names, voice-role folders, and explicit named text references.

Do not reduce the catalog to names. Preserve role/status, gender/age, character description, performance notes, source evidence, first appearance, and approved relationships.

Treat an out-of-catalog named answer as a review item. Never invent a personal name.

## Generic speakers

Use stable Japanese production labels:

- `男性音声` or `女性音声` when only one generic speaker of that type occurs;
- `男性音声01`, `男性音声02`, `女性音声01`, and similar numbered labels when several must remain distinct;
- `子供音声01`, `老人男性音声01`, or `老人女性音声01` when clearly supported;
- `ナレーション` for narration;
- `不明音声` only when identity, type, and continuity cannot be established.

Never force an unnamed speaker onto a named cast member.

Maintain an episode-level generic-speaker registry. Give each distinct unnamed speaker one stable `generic_speaker_key`. One key maps to one role, and one role must not hide several distinct keys.

## Relationship evidence

Build relationship edges only from explicit named statements. Preserve source field and source text, and mark inverse edges as derived.

An explicit named relative outside the approved catalog may remain `unapproved_reference`. It may narrow an approved candidate but must never become `final_role`.

Retrieve relationship evidence by complete semantic unit. Use it to narrow and test candidates, never to auto-assign or hard-exclude. A missing edge means not structured, not impossible.

Explicit source dialogue, audio, or continuous video overrides the person table. Record `relationship_conflict=true`, the exact conflicting edge, and the resolution when this occurs.

## Cross-language subtitles

When source audio is Chinese or English and the working SRT is Japanese:

- use Japanese text to form semantic units and initial candidates;
- do not treat translated wording as literal source-language proof;
- escalate omitted subjects, changed address forms, softened tone, or rewritten speaker-relevant meaning;
- use reviewed original-language acoustic evidence when accepted by the beta1.5 quality gates;
- otherwise inspect continuous source video/audio.

## One-shot face references

One user-supplied clear screenshot per role is normally sufficient. Use the user's role-to-image mapping as identity authority.

Face matching answers who is visible, not who speaks. Perform it only when continuous video is opened for an escalated unit.

Record:

- `matched`;
- `ambiguous`;
- `no_face`;
- `low_quality`;
- `not_available`;
- `not_reviewed`.

Treat profile, occluded, tiny, blurred, heavily restyled, and look-alike cases as `ambiguous` or `low_quality`. Never force the closest match.

A visible silent listener is evidence for a reaction shot or off-screen speaker, not for the listener speaking.

## Semantic and turn review

1. Read the complete semantic unit plus at least two preceding and two following lines.
2. Use vocatives, pronouns, question-answer structure, self-reference, relationships, knowledge ownership, intent, grammar, and stable turn-taking.
3. Join rows only when punctuation, grammar, or an unclosed construction strongly proves continuation.
4. Do not infer a speaker change from a subtitle boundary, zero gap, or camera cut.
5. Keep the same confirmed speaker through reaction shots and cutaways until audible reply, completed grammar, lip onset, entrance/exit, or scene transition proves a change.
6. Require specific `turn_change_evidence` for every role change inside a definite or likely continuation.
7. Recheck A-B-A flips, self-address, impossible knowledge, named/generic drift, and sentence-internal changes.

`semantic_unit` and source-language `acoustic_turn` are separate segmentations. Record their relation as:

- `aligned`;
- `semantic_split`;
- `semantic_merge`;
- `mixed`;
- `review`.

Probe only a reviewed continuous non-overlapping `single_speaker` acoustic turn.

## Error-prevention review tips

These are review triggers and guardrails, not a new evidence hierarchy. Apply the normal multi-evidence decision method after each triggered inspection; no tip alone assigns or changes a role.

1. **On-screen-text timestamps are hard temporal boundaries.** A timed on-screen marker for a flashback, scene transition, location change, or comparable event begins only at its actual display timestamp. Never generalize it backward because an earlier line contains a matching word, prop, or motif. Any forward extension still requires continuous scene evidence and ends at the next verified boundary.
2. **Direct honorifics are a high-priority logical guardrail.** Check reliable direct address against the approved relationship and honorific dictionary. A conflicting direct address must overturn keyword matching, anonymous acoustic clustering, or a voiceprint candidate—for example, `兄上` / `皇嫂様`, `陛下`, or `阿昭` / `夫君` when those usages are already confirmed for the story. First verify who speaks, who is addressed, and whether the wording is direct speech rather than quotation, imitation, flashback, or translation drift; an honorific alone does not auto-assign identity.
3. **Short interjections never inherit the previous speaker mechanically.** For a sub-one-second interjection, reaction sound, or breath such as `あっ…`, `フン`, `えっ`, or a sigh, inspect its audible onset and voice continuity, visible lip activity, reaction-shot status, emotional ownership, and surrounding semantic unit. The result may remain with the previous speaker, switch to the reacting party, or abstain; a visible reaction face or inferred mental state alone is not speaking proof.
4. **Do not merge acoustic turns across scene or visual boundaries.** Potential speaker changes, shot/reverse-shot uncertainty, reality/flashback intercuts, off-screen-voice changes, or hard scene boundaries require sentence-level or exact-shot review and separate acoustic turns where identity may change. Any acoustic turn longer than ten seconds requires explicit reinspection; retain it only when continuous evidence confirms one speaker, one scene, no overlap, and no identity transition. Duration alone does not force a valid continuous monologue to be split.

## Evidence priority

Use:

1. preparation-stage anonymous acoustic turns and user-confirmed three-model voice-anchor evidence, when available and accepted by its quality gates;
2. complete Japanese semantic/context screening as the required independent cross-check;
3. continuous source video/audio when acoustic evidence is absent, weak, mixed, short, overlapping, or conflicting;
4. TalkNet active-speaker results when a reliably mapped face is visibly speaking, or as negative support when a visible reaction face is not speaking;
5. relationships and one-shot face identity as supporting evidence.

No single modality may overwrite a role automatically.

### beta1.5 first-episode anchor workflow

Use dialogue logic and user-confirmed voice identity as the two primary evidence streams after the mandatory first-episode confirmation gate. For later episodes, score confirmed voice identity first, then independently check the complete dialogue context. For the first episode, use dialogue/video evidence to prepare the mapping for user confirmation; no named gallery exists yet.

Use picture and TalkNet evidence only to support presence, entrance/exit, obvious lip activity, reaction/off-screen classification, phone/flashback context, and scene boundaries. TalkNet remains normal supporting evidence in this mode; it is not a separate branch and cannot identify an off-screen speaker.

For a named role from the approved internal character reference, a first speaking appearance or missing, unreliable, close, or conflicting voice evidence opens an episode-boundary review gate. Do not interrupt at that row: keep the assignment provisional, finish every source row in the current episode, aggregate every new or unresolved named role, and then pause once with a full-episode role review sheet. Do not continue later episodes until the user confirms or edits the complete episode mapping and the episode is rechecked. Silent visual appearances and generic unnamed roles do not open this gate.

This is the single beta1.5 workflow. Voice remains supporting evidence and never assigns a role automatically, even after all user confirmations are resolved.

## External first-draft audit

When another AI supplies a first-version script, preserve it unchanged and treat its role labels as candidate claims only. Normalize it against the canonical `(episode, source_index, start, end, text)` rows before review; missing, duplicate, extra, reordered, or rewritten rows are mapping defects, not speaker evidence.

Keep `draft_role`, `draft_agreement`, and `draft_disagreement_reason` alongside the corrected internal rows when available. Agreement with the draft still requires row-specific evidence. A disagreement must be resolved from the normal evidence hierarchy and recorded rather than silently replacing the draft.

## Required delivery evidence

Every client-delivery row requires:

- `review_confidence`;
- `semantic_unit`;
- row-specific `semantic_evidence`;
- `visual_class`;
- `identity_status` and row-specific `identity_evidence`;
- `face_reference_available` and `face_match_status`;
- `generic_speaker_key` for generic roles;
- `coarse_audio_status` and `audio_conflict`;
- TalkNet status, mapped candidate, and abstention/conflict reason when TalkNet was run;
- acoustic-turn fields when acoustic review is opened;
- `turn_change_evidence` when required;
- `relationship_evidence` and `relationship_conflict`;
- `conflict_resolution` for reaction, off-screen, cutaway, mixed, unclear, or conflicting cases;
- `reviewed_by`;
- `reviewer_note`.

Cap generic or unconfirmed identity confidence at `0.89`. Reserve `0.90` and above for named identity with explicit evidence.

Reject reviews that use repeated boilerplate, mechanical confidence values, silent conflict acceptance, incomplete row coverage, or a whole range marked uncertain because work was unfinished.

## Independent final audit

Export a blind audit after the complete internal episode draft and before formal delivery. The auditor must not see generated roles, confidence, or reasoning.

The auditor assigns roles from source dialogue, eligible acoustic evidence when available, and continuous video/audio for unresolved or conflicting cases.

Only full agreement may attach to the final TSV. Every disagreement returns to normal review and invalidates the previous audit. Never let the audit overwrite a role automatically.

Exception: a row imported from an explicitly approved whole-episode user workbook with complete file provenance and a valid `user_locked=true` / `user_locked_role` pair does not need a second internal blind audit. The user's workbook decision is the final human authority for that locked row. An instruction to continue after the ordinary review workbook was supplied counts as unchanged approval and must be recorded in `approval_note`. This exception never applies merely because a confirmation workbook exists; `approval_mode` and the approval event must be recorded, and every unlocked row still requires the normal blind audit.
