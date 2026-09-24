#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subtitle Alignment Skill V26 (Chunked Local Alignment)
浼樺寲涓庨噸鏋勭偣锛?1. 闈炵牬鍧忔€?SRT 娓呮礂涓庡缂栫爜鑷姩妫€娴?(Phase 0) - 鑷€傚簲鎺㈡祴澶氱缂栫爜锛屼笉淇敼婧愭枃浠讹紝浣跨敤瀹夊叏涓存椂鏂囦欢
2. 绮惧噯闆嗘暟瀛楀箷姝ｅ垯鍖归厤涓庢樉寮忔姤閿?- 鍓旈櫎骞叉壈锛岀己澶辨枃浠舵姏鍑鸿鍛婂苟鏍囪澶辫触
3. 椴佹鎬у瓧绗﹀尮閰嶄笌鎸囬拡瀹夊叏鎺ㄧЩ - 杩囨护鏍囩偣锛?0% 纭尮閰嶇巼闄愬埗锛岀簿鍑嗘寜 last_match_idx + 1 鎺ㄧЩ
4. 绾犳 VAD 缁撳熬淇閫昏緫 - 娑堥櫎 min() 闄愬埗鐭涚浘锛岃瀹氭渶澶?0.8s 寤跺睍鍜屾渶灏?0.05s 瀛楃瀹夊叏杈圭晫
5. 鍓嶅悜鎺ㄨ繘锛團orward-Push锛夎В閲嶅彔涓庢椂闀夸繚璇佺畻娉?- 涓ユ牸鏃跺簭璋冩暣锛岀淮鎸?0.4s 鏈€灏忔椂闀夸笌 33ms 瀹夊叏甯ч棿闅?6. 澧炲己 QC 璐ㄦ瑕嗙洊搴?- 鏈垚鍔熷榻愯榛樿鏍囪锛屽鍔?HIGH_DENSITY 涓?LOW_DENSITY 瀵嗗害鍒嗘瀽
7. 鎵瑰鐞嗕笁鎬佽褰曚笌璇︾粏鍏冩暟鎹?json 绯荤粺 - 寮曞叆 passed / needs_review / failed 鐘舵€?8. WhisperX 灏鹃煶骞昏鐔旀柇鏈哄埗 (Hallucination Guard) - 妫€娴嬪苟鍒囨柇澶т簬 0.6s 鐨勫瓧绗︾骇 AI 鎷変几骞昏
9. Librosa 闃茶繃搴﹀垏闄ゅ厹搴?(Over-truncation Guard) - 鐗╃悊杈圭晫杩囧害鍒囬櫎鏃惰嚜鍔ㄦ仮澶?WhisperX 璇箟杈圭晫锛岄槻姝㈠悶瀛?10. 瀹夊叏瀛楀箷鏃堕暱姣斾緥淇濇姢 - 鍦ㄥ瓧鏁板浣嗘椂闀挎瀬鐭椂杩涜鏁板骞虫粦鎷変几锛屾潨缁濊法闊抽宀涜烦杞?"""

import shutil
import os
import sys
import glob
import re
import json
import time
import argparse
import torch
import librosa
import pysrt
import whisperx
from faster_whisper import WhisperModel
import numpy as np

WHISPER_MODEL_ROOT = "/mnt/e/AI_Models/Whisper"
WHISPER_QC_MODEL = "medium"

_model_a = None
_metadata = None
_qc_model = None
_vad_model = None
_vad_utils = None

def fmt(seconds):
    if seconds < 0: seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()

# ==================== Phase 0: SRT Pre-processing ====================

def decode_bytes(content_bytes):
    for enc in ['utf-8-sig', 'utf-8', 'cp932', 'shift_jis', 'utf-16']:
        try:
            return content_bytes.decode(enc), enc
        except UnicodeDecodeError:
            continue
    log("[WARNING] Failed to decode SRT file with standard encodings. Falling back to utf-8 with 'replace'.")
    return content_bytes.decode('utf-8', errors='replace'), 'utf-8_replace'

def clean_and_fix_srt(srt_path, temp_dir):
    log(f"Phase 0: Pre-processing SRT: {os.path.basename(srt_path)}")
    with open(srt_path, 'rb') as f:
        content_bytes = f.read()
    
    content, enc = decode_bytes(content_bytes)
    log(f"  Decoded with encoding: {enc}")
    
    # 瑙ｇ爜鍚庡啀瑙勮寖鍖栨崲琛岋紝閬垮厤鍦?cp932/shift_jis/utf-16 瀛楄妭娴佷笂璇敼鍐呭
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    lines = content.split('\n')
    fixes_needed = False
    for i, line in enumerate(lines):
        line = line.strip()
        if re.match(r'^\d+$', line):
            if i > 0 and lines[i-1].strip() != '':
                fixes_needed = True
                break
                
    if fixes_needed:
        log("  [!] Corrupted SRT structure detected. Fixing...")
        fixed_content = re.sub(r'(\S[^\n]*)(\n)(\d{1,4})(\n\d{2}:\d{2}:\d{2},\d{3} -->)', 
                               r'\1\n\n\3\4', content)
    else:
        fixed_content = content
        
    os.makedirs(temp_dir, exist_ok=True)
    temp_srt_path = os.path.join(temp_dir, f"cleaned_{os.path.basename(srt_path)}")
    with open(temp_srt_path, 'w', encoding='utf-8-sig') as f:
        f.write(fixed_content)
        
    log(f"  Cleaned SRT written to temporary file: {temp_srt_path}")
    return temp_srt_path

def strip_punctuation(text):
    return re.sub(r'[\s銆傘€侊紒锛??鈥.,\-\"\']', '', text)

def get_row_chars_robust(clean_text, char_timeline, start_search_idx):
    alignable_text = strip_punctuation(clean_text)
    if not alignable_text:
        return [], start_search_idx
        
    matched_chars = []
    idx = start_search_idx
    for target_char in alignable_text:
        found = False
        for lookahead in range(40):
            t_pos = idx + lookahead
            if t_pos >= len(char_timeline):
                break
            if char_timeline[t_pos]['char'] == target_char:
                matched_chars.append(char_timeline[t_pos])
                idx = t_pos + 1
                found = True
                break
                
    ratio = len(matched_chars) / len(alignable_text)
    
    # 璁惧畾鍖归厤鐜囬棬妲涳紝浣庝簬 40% 鍒ゅ畾涓鸿瀵归綈澶辫触锛屼笉鎺ㄨ繘 current_pos
    if len(matched_chars) > 0 and ratio >= 0.40:
        last_match_idx = char_timeline.index(matched_chars[-1])
        new_search_idx = last_match_idx + 1
        return matched_chars, min(new_search_idx, len(char_timeline))
    else:
        # Match ratio too low; do not advance the search index.
        return [], start_search_idx

# ==================== Phase 1: AI VAD (Silero VAD) ====================

def get_islets_silero(audio_path, device="cuda"):
    global _vad_model, _vad_utils
    if _vad_model is None:
        log("  Loading Silero VAD model...")
        _vad_model, _vad_utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', force_reload=False)
        _vad_model = _vad_model.to(device)
    
    (get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = _vad_utils
    wav = read_audio(audio_path, sampling_rate=16000).to(device)
    speech_timestamps = get_speech_timestamps(wav, _vad_model, sampling_rate=16000, threshold=0.3)
    
    islet_times = [(ts['start']/16000.0, ts['end']/16000.0) for ts in speech_timestamps]
    return islet_times

# ==================== Core Alignment ====================

def align_one(audio_path, srt_path, output_path, vad_mode="hybrid"):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(f"Processing: {os.path.basename(audio_path)} [V26 (Chunked Local Alignment)]")
    
    # Phase 0: non-destructive preprocessing, uses temp files under temp_dir.
    temp_dir = os.path.join(os.path.dirname(output_path), ".temp")
    temp_srt_path = clean_and_fix_srt(srt_path, temp_dir)
    
    try:
        # Phase 1: VAD 鍒嗘瀽
        log(f"  Phase 1: VAD analysis (Mode: {vad_mode})...")
        y, sr = librosa.load(audio_path, sr=16000)
        
        silero_islets = []
        librosa_islets = []
        
        if vad_mode in ["hybrid", "silero"]:
            log("    Running AI VAD (Silero) semantic analysis...")
            silero_islets = get_islets_silero(audio_path, device=device)
            
        if vad_mode in ["hybrid", "librosa"]:
            log("    Running Librosa energy analysis...")
            islets = librosa.effects.split(y, top_db=25)
            librosa_islets = [(s/sr, e/sr) for s, e in islets]
            
        if vad_mode == "librosa":
            islet_times = librosa_islets
        else:
            islet_times = silero_islets
            
        duration_total = len(y) / sr
        
        subs_orig = pysrt.open(temp_srt_path, encoding='utf-8-sig')
            
        # Phase 2: WhisperX (Chunked Local Alignment V26)
        log("  Phase 2: WhisperX chunked local anchoring...")
        audio = whisperx.load_audio(audio_path)
        
        global _model_a, _metadata
        if _model_a is None or _metadata is None:
            _model_a, _metadata = whisperx.load_align_model(language_code="ja", device=device)
        
        # Build Super Segments (Overlapping 15s+ chunks) to prevent Wav2Vec2 drift
        super_segments = []
        current_text = ""
        chunk_start = 0.0
        last_end = 0.0
        
        for i, sub in enumerate(subs_orig):
            clean_text = sub.text.replace("\n", "").replace(" ", "").strip()
            if not clean_text:
                continue
                
            s_orig = sub.start.ordinal / 1000.0
            e_orig = sub.end.ordinal / 1000.0
            
            if current_text == "":
                chunk_start = max(0.0, s_orig - 2.0)
                
            current_text += clean_text
            last_end = e_orig
            
            # Check if we should cut the chunk (duration >= 12s and gap >= 0.5s, or force cut at 25s)
            cut = False
            if i < len(subs_orig) - 1:
                next_sub = subs_orig[i+1]
                next_s = next_sub.start.ordinal / 1000.0
                gap = next_s - e_orig
                duration_so_far = e_orig - chunk_start
                if duration_so_far >= 12.0 and gap >= 0.5:
                    cut = True
                elif duration_so_far >= 25.0:
                    cut = True
            else:
                cut = True
                
            if cut:
                chunk_end = min(duration_total, last_end + 2.0)
                super_segments.append({
                    "text": current_text,
                    "start": chunk_start,
                    "end": chunk_end
                })
                current_text = ""
                
        log(f"    Built {len(super_segments)} super segments for drift-free alignment.")
        aligned = whisperx.align(super_segments, _model_a, _metadata, audio, device, return_char_alignments=True)
        
        log("  Phase 3: Extracting character timeline...")
        char_timeline = []
        for seg in aligned.get("segments", []):
            if 'chars' in seg:
                for c in seg['chars']:
                    if 'char' in c and 'start' in c and 'end' in c:
                        char_timeline.append(c)
                        
        log(f"  Phase 4: Row-by-row alignment with {vad_mode.upper()} VAD bounds...")
        results = []
        current_pos = 0
        
        for sub in subs_orig:
            clean_text = sub.text.replace("\n", "").replace(" ", "").strip()
            char_count = len(strip_punctuation(clean_text))
            if char_count == 0: char_count = 1
            
            row_chars, current_pos = get_row_chars_robust(clean_text, char_timeline, current_pos)
            
            if row_chars:
                s_time = row_chars[0]['start']
                ai_e_time = row_chars[-1]['end']
                
                # 闃插尽 WhisperX 灏鹃煶骞昏锛堣嫢鍗曞瓧瓒呰繃0.6s锛屽己鍒舵埅鏂級
                last_char_dur = row_chars[-1]['end'] - row_chars[-1]['start']
                last_char_hallucinated = False
                if last_char_dur > 0.6:
                    ai_e_time = row_chars[-1]['start'] + 0.3
                    last_char_hallucinated = True
                
                # 闄愬埗 WhisperX 瀵瑰噯瀛楃鏈€澶ц法搴︼紝闃叉鍦?long silence 鍥犳潅闊?骞诲惉瀵艰嚧鎷変几
                max_dur = char_count * 0.35 + 1.2
                if ai_e_time - s_time > max_dur:
                    ai_e_time = s_time + max_dur
                
                # 1. locate a matching Silero semantic islet
                matched_islet_idx = -1
                for i, (i_s, i_e) in enumerate(silero_islets):
                    if i_s <= s_time + 0.15 <= i_e:
                        matched_islet_idx = i
                        break
                        
                # 2. Find the matching Librosa physical islet.
                matched_l_e = None
                if vad_mode == "hybrid":
                    for i, (l_s, l_e) in enumerate(librosa_islets):
                        if matched_islet_idx != -1:
                            s_s, s_e = silero_islets[matched_islet_idx]
                            # Keep Librosa candidates inside the Silero semantic islet.
                            if l_e < s_s - 0.2 or l_s > s_e + 0.2:
                                continue
                        if l_s <= ai_e_time + 0.1:
                            matched_l_e = l_e
                            
                # 3. 璁惧畾鍒濆缁撳熬鏃堕棿
                if vad_mode == "hybrid" and matched_l_e is not None:
                    # Avoid cutting weak endings too early unless WhisperX hallucinated.
                    if not last_char_hallucinated and matched_l_e < ai_e_time - 0.2:
                        e_time = ai_e_time + 0.05
                    else:
                        e_time = matched_l_e
                elif matched_islet_idx != -1:
                    e_time = islet_times[matched_islet_idx][1]
                else:
                    e_time = ai_e_time
                
                # 灏忎績闊虫墿灞曢€昏緫
                has_sokuon = 'っ' in clean_text or 'ッ' in clean_text
                if has_sokuon and matched_islet_idx != -1 and matched_islet_idx < len(islet_times) - 1:
                    next_s, next_e = islet_times[matched_islet_idx + 1]
                    if (next_s - e_time) < 0.300:
                        e_time = next_e
                
                # 瀛楁暟涓庢椂闀夸笉瓒虫墿灞曢€昏緫
                if char_count > 5 and (e_time - s_time) < (char_count * 0.06):
                    # 寮鸿淇濆簳鏃堕暱锛岄槻姝㈣烦鍒颁笅涓€涓繙澶勭殑宀涘笨
                    e_time = s_time + char_count * 0.06
                
                # 4. final dual-channel/dual-engine physical cross-check safety-apply
                if vad_mode == "hybrid":
                    if matched_l_e is not None:
                        e_time = min(ai_e_time + 0.10, e_time + 0.05)
                    else:
                        e_time = ai_e_time + 0.10
                elif vad_mode == "silero":
                    if matched_islet_idx != -1:
                        i_e = silero_islets[matched_islet_idx][1]
                        # 鍙岄€氶亾浜ゅ弶鏍￠獙锛岄槻骞诲惉鎷変几
                        if ai_e_time <= i_e + 0.8:
                            e_time = max(ai_e_time + 0.05, min(e_time, ai_e_time + 0.10))
                        else:
                            e_time = i_e + 0.05
                    else:
                        e_time = ai_e_time + 0.10
                else:  # librosa mode
                    if matched_islet_idx != -1:
                        i_e = librosa_islets[matched_islet_idx][1]
                        e_time = min(ai_e_time + 0.10, e_time + 0.05)
                    else:
                        e_time = ai_e_time + 0.10
                    
                if e_time < s_time + 0.3:
                    e_time = s_time + 0.6
                is_matched = True
            else:
                # 寮哄埗瀵瑰噯澶辫触琛屽洖閫€
                s_time = (results[-1][1] + 0.1) if results else 0.5
                e_time = s_time + 1.5
                is_matched = False
                
            results.append([s_time, e_time, is_matched])
            
        # Phase 5: Forward-Push overlap resolution & minimum duration solver
        log("  Phase 5: Forward-Push overlap resolution & minimum duration solver...")
        MIN_GAP = 0.033
        MIN_DURATION = 0.4
        
        # Step 1: 鏃跺簭寮虹害鏉燂紝淇濊瘉寮€濮嬫椂闂翠弗鏍奸€掑
        for i in range(len(results) - 1):
            if results[i+1][0] < results[i][0] + 0.1:
                results[i+1][0] = results[i][0] + 0.1
                
        # Step 2: resolve overlaps while preserving V26 minimum duration.
        for i in range(len(results)):
            s, e, matched = results[i]
            duration = e - s
            if duration < MIN_DURATION:
                results[i][1] = s + MIN_DURATION
                
            if i < len(results) - 1:
                next_start = results[i+1][0]
                current_end = results[i][1]
                if current_end > next_start - MIN_GAP:
                    limit = results[i][0] + MIN_DURATION
                    if next_start - MIN_GAP >= limit:
                        results[i][1] = next_start - MIN_GAP
                    else:
                        # 寮鸿淇濇寔褰撳墠鍙ユ渶灏忔椂闀匡紝骞舵妸涓嬩竴鍙ョ殑寮€澶村線鍚庢帹
                        results[i][1] = limit
                        results[i+1][0] = limit + MIN_GAP
                        
        log("  Phase 6: Saving aligned SRT...")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if os.path.exists(output_path):
            import shutil
            backup_path = output_path.replace(".srt", f"_bak_{int(time.time())}.srt")
            try:
                shutil.copyfile(output_path, backup_path)
                log(f"  [Backup] Existing SRT backed up to: {os.path.basename(backup_path)}")
            except Exception as backup_err:
                log(f"  [!] Failed to back up existing SRT: {backup_err}")
                
        with open(output_path, "w", encoding="utf-8-sig") as f:
            for idx, (sub, res) in enumerate(zip(subs_orig, results), 1):
                s, e = res[0], res[1]
                f.write(f"{idx}\n{fmt(s)} --> {fmt(e)}\n{sub.text}\n\n")
                
        return subs_orig, results, y, sr, char_timeline, islet_times
        
    finally:
        # 娓呯悊涓存椂鏂囦欢
        if os.path.exists(temp_srt_path):
            try:
                os.remove(temp_srt_path)
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass

# ==================== Phase 7 & 7.5: QC and Auto-Fix ====================

def quality_check_and_autofix(subs_orig, results, y_full, sr, islet_times, output_path):
    log("\n  Phase 7: Intelligent Quality Check...")
    global _qc_model
    if _qc_model is None:
        _qc_model = WhisperModel(WHISPER_QC_MODEL, device="cuda" if torch.cuda.is_available() else "cpu", compute_type="float16", download_root=WHISPER_MODEL_ROOT)
    
    flagged = []
    for i in range(len(results)):
        s, e, is_matched = results[i][:3]
        duration = e - s
        text = subs_orig[i].text.replace("\n", " ").strip()
        clean_text = subs_orig[i].text.replace("\n", "").replace(" ", "").strip()
        char_count = len(strip_punctuation(clean_text))
        
        s_sample = int(s * sr)
        e_sample = int(e * sr)
        segment = y_full[s_sample:e_sample]
        
        if len(segment) == 0:
            flagged.append({'index': i+1, 'text': text, 'reasons': ["EMPTY"], 'rms':0, 'run_before':0, 'whisper_heard':'', 'start':s, 'end':e})
            continue
            
        rms = float(np.sqrt(np.mean(segment**2)))
        gap_before = (s - results[i-1][1]) if i > 0 else s
        
        is_suspicious = False
        reasons = []
        
        # 1. Physical and silence checks.
        if rms < 0.005:
            is_suspicious = True; reasons.append(f"SILENT(RMS={rms:.5f})")
        if gap_before > 3.0 and duration < 0.5:
            is_suspicious = True; reasons.append(f"LONG_GAP({gap_before:.1f}s)")
        if duration < 0.2:
            is_suspicious = True; reasons.append(f"ULTRA_SHORT({duration:.3f}s)")
            
        # 2. Unmatched WhisperX rows are suspicious.
        if not is_matched:
            is_suspicious = True; reasons.append("NO_WHISPERX_MATCH")
            
        # 3. Speaking-rate density checks.
        density = char_count / duration if duration > 0 else 0
        if density > 12.0:
            is_suspicious = True; reasons.append(f"HIGH_DENSITY({density:.1f}c/s)")
        if density < 2.0 and duration > 1.5:
            is_suspicious = True; reasons.append(f"LOW_DENSITY({density:.1f}c/s)")
            
        whisper_heard = ""
        if is_suspicious:
            padded = np.pad(segment, (0, max(0, sr - len(segment))), mode='constant').astype(np.float32)
            segments_iter, _ = _qc_model.transcribe(padded, language="ja")
            whisper_heard = ''.join([seg.text for seg in segments_iter]).strip()
            
            if whisper_heard == "" or whisper_heard in {".", "..", "..."}:
                reasons.append("WHISPER_EMPTY")
            else:
                c_exp = text.replace(" ", "").replace("!", "").replace("?", "")
                c_heard = whisper_heard.replace(" ", "").replace("!", "").replace("?", "")
                overlap = sum(1 for c in c_exp if c in c_heard)
                ratio = overlap / max(len(c_exp), 1)
                if ratio < 0.3:
                    reasons.append(f"MISMATCH(overlap={ratio:.0%})")
            
            flagged.append({'index': i+1, 'text': text, 'reasons': reasons, 'rms': rms, 'gap_before': gap_before, 'whisper_heard': whisper_heard, 'start': s, 'end': e})
            
    # 鍐欏叆 QC Report
    qc_dir = os.path.join(os.path.dirname(output_path), "qc_reports")
    os.makedirs(qc_dir, exist_ok=True)
    output_stem = os.path.splitext(os.path.basename(output_path))[0]
    qc_report_path = os.path.join(qc_dir, f"{output_stem}_QC_REPORT.txt")
    report_lines = [f"QC Report | Total Lines: {len(results)} | Flagged: {len(flagged)}", "="*50]
    for f in flagged:
        report_lines.append(f"[FLAG] Line {f['index']}: {f['text']} | {' | '.join(f['reasons'])} | Heard: {f['whisper_heard']}")
    with open(qc_report_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(report_lines))
        
    # Phase 7.5: Auto-Fix
    log("\n  Phase 7.5: Heuristic Auto-Fix...")
    fixes = 0
    fix_logs = []
    
    for f in flagged:
        idx = f['index'] - 1
        s, e = results[idx][:2]
        reason_str = "".join(f['reasons'])
        
        if "SILENT" in reason_str or "WHISPER_EMPTY" in reason_str:
            old_e = e
            # 绛栫暐 B: 鐣欏湪鍘熷湴涓嶅姩锛屾椂闂村帇缂╁埌鏋佺煭 (0.5s)锛岄伩鍏嶇牬鍧忔暣浣撴椂闂磋酱
            if e - s > 0.5:
                results[idx][1] = s + 0.5
                fixes += 1
                msg = f"Line {f['index']} Auto-fixed: Compressed duration to 0.5s (End {fmt(old_e)} -> {fmt(results[idx][1])})"
                log(f"    [FIX] {msg}")
                fix_logs.append(msg)
    
    if fixes > 0:
        with open(output_path, "w", encoding="utf-8-sig") as f_out:
            for i, (sub, res) in enumerate(zip(subs_orig, results), 1):
                s, e = res[0], res[1]
                f_out.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n{sub.text}\n\n")
        
        fix_log_path = os.path.join(qc_dir, f"{output_stem}_AUTO_FIX_LOG.txt")
        with open(fix_log_path, "w", encoding="utf-8-sig") as f_out:
            f_out.write("\n".join(fix_logs))
    else:
        log("    [OK] No auto-fixes needed/applied.")
        
    return len(flagged), len(results)

# ==================== Batch Execution ====================

def find_matching_srt(sub_dir, ep_num):
    num_pad = str(ep_num).zfill(2)
    all_srts = glob.glob(os.path.join(sub_dir, "*.srt"))
    matched_files = []
    
    for srt_path in all_srts:
        filename = os.path.basename(srt_path)
        p1 = rf"^{ep_num}(?!\d)"
        p2 = rf"^{num_pad}(?!\d)"
        p3 = rf"(?:^|[\s_\[\-\(]|ep|EP){ep_num}(?!\d)"
        p4 = rf"(?:^|[\s_\[\-\(]|ep|EP){num_pad}(?!\d)"
        
        if re.search(p1, filename) or re.search(p2, filename) or re.search(p3, filename) or re.search(p4, filename):
            matched_files.append(srt_path)
            
    return list(dict.fromkeys(matched_files))

def record_episode_status(progress_data, progress_file, num_pad, status, **metadata):
    progress_data.setdefault("completed", [])
    progress_data.setdefault("episodes", {})
    progress_data["episodes"][num_pad] = {
        "status": status,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        **metadata
    }
    
    # Treat passed and needs_review as completed for resume purposes.
    if status in ["passed", "needs_review"]:
        if num_pad not in progress_data["completed"]:
            progress_data["completed"].append(num_pad)
    else:
        progress_data["completed"] = [ep for ep in progress_data["completed"] if ep != num_pad]
        
    with open(progress_file, 'w') as f:
        json.dump(progress_data, f, indent=2, ensure_ascii=False)

def batch_process(target_eps=None, vad_mode="hybrid"):
    base_dir = "/mnt/e/鐭墽閰嶉煶瀛楀箷瀵归綈"
    vocal_dir = os.path.join(base_dir, "vocal")
    sub_dir = os.path.join(base_dir, "subtitle(no alignment)")
    out_dir = os.path.join(base_dir, "output")
    os.makedirs(out_dir, exist_ok=True)
    
    progress_file = os.path.join(out_dir, ".progress_v26.json")
    
    progress_data = {"completed": [], "episodes": {}}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
                if "completed" not in progress_data:
                    progress_data["completed"] = []
                if "episodes" not in progress_data:
                    progress_data["episodes"] = {}
        except: pass
        
    wavs = sorted(glob.glob(os.path.join(vocal_dir, "*.wav")))
    
    for wav_path in wavs:
        match = re.search(r'\d+', os.path.basename(wav_path))
        if not match: continue
        ep_num = int(match.group())
        num_pad = str(ep_num).zfill(2)
        
        if target_eps and ep_num not in target_eps: continue
        episode_status = progress_data.get("episodes", {}).get(num_pad, {}).get("status")
        if episode_status in ["passed", "needs_review"]:
            log(f"[SKIP] Episode {num_pad} already processed (Status: {episode_status}).")
            continue
            
        srts = find_matching_srt(sub_dir, ep_num)
        if not srts:
            msg = f"No matching SRT file found in '{sub_dir}'"
            log(f"[WARNING] Episode {num_pad}: {msg}!")
            record_episode_status(progress_data, progress_file, num_pad, "failed", error=msg)
            continue
            
        if len(srts) > 1:
            candidates = [os.path.basename(p) for p in srts]
            msg = "Multiple matching SRT files found; choose one explicitly before running"
            log(f"[WARNING] Episode {num_pad}: {msg}: {candidates}")
            record_episode_status(
                progress_data,
                progress_file,
                num_pad,
                "failed",
                error=msg,
                candidates=candidates
            )
            continue
        
        out_path = os.path.join(out_dir, f"ALIGNED_EP{num_pad}_V26.srt")
        
        try:
            # 杩愯鏍稿績瀵归綈寮曟搸
            subs_orig, results, y, sr, char_timeline, islet_times = align_one(wav_path, srts[0], out_path, vad_mode=vad_mode)
            
            # 杩愯澧炲己鐗堟櫤鑳?QC 骞惰繑鍥?flagged 缁熻
            flagged_count, total_lines = quality_check_and_autofix(subs_orig, results, y, sr, islet_times, out_path)
            
            status = "passed" if flagged_count == 0 else "needs_review"
            record_episode_status(
                progress_data,
                progress_file,
                num_pad,
                status,
                flagged_count=flagged_count,
                total_lines=total_lines,
                srt_file=os.path.basename(srts[0]),
                vad_mode=vad_mode
            )
                
            log(f"[SUCCESS] Episode {num_pad} processed. Status: {status.upper()} (Flagged: {flagged_count}/{total_lines})")
            
        except Exception as e:
            log(f"[!] Error on Ep {num_pad}: {e}")
            import traceback
            traceback.print_exc()
            record_episode_status(progress_data, progress_file, num_pad, "failed", error=str(e))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ep", type=int, nargs="+", help="Specify episodes")
    parser.add_argument("--no-silero", action="store_true", help="Fallback to librosa VAD")
    parser.add_argument("--mode", type=str, choices=["hybrid", "silero", "librosa"], default="hybrid",
                        help="VAD Mode: 'hybrid' (Silero + Librosa, default), 'silero' (Silero only), 'librosa' (Librosa only)")
    args = parser.parse_args()
    
    vad_mode = args.mode
    if args.no_silero:
        vad_mode = "librosa"
        
    batch_process(args.ep, vad_mode=vad_mode)


