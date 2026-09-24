[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias("Input")]
    [string[]]$Sources,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    [ValidateSet("Other", "Vocals")]
    [string]$Stem = "Other",

    [ValidateSet("Auto", "MelBand", "BSRoformer")]
    [string]$Model = "Auto",

    [string]$Distro = "Ubuntu",

    [string]$AiModelsRootWsl = "/mnt/e/AI_Models",

    [string]$FfmpegWsl = "ffmpeg",

    [string]$Scale,

    [switch]$Overwrite,

    [switch]$KeepWork,

    [switch]$UseAutocast
)

$ErrorActionPreference = "Stop"

$videoExtensions = @(".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts")
$audioExtensions = @(".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".wma")
$supportedExtensions = $videoExtensions + $audioExtensions

function Convert-ToWslPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath -notmatch "^(?<drive>[A-Za-z]):\\(?<tail>.*)$") {
        throw "WSL path conversion requires an absolute Windows path: $Path"
    }

    $drive = $Matches["drive"].ToLowerInvariant()
    $tail = $Matches["tail"] -replace "\\", "/"
    return "/mnt/$drive/$tail"
}

function Invoke-WslCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & wsl.exe -d $Distro --exec $Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code $LASTEXITCODE"
    }
}

function Get-SourceFiles {
    param([Parameter(Mandatory = $true)][string[]]$Paths)

    $files = foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Input does not exist: $path"
        }

        $item = Get-Item -LiteralPath $path
        if ($item.PSIsContainer) {
            Get-ChildItem -LiteralPath $item.FullName -File |
                Where-Object {
                    $supportedExtensions -contains $_.Extension.ToLowerInvariant() -and
                    (-not $_.Name.StartsWith("._"))
                }
        }
        elseif ($supportedExtensions -contains $item.Extension.ToLowerInvariant()) {
            $item
        }
        else {
            throw "Unsupported input type: $($item.FullName)"
        }
    }

    return @($files | Sort-Object FullName -Unique)
}

$files = Get-SourceFiles -Paths $Sources
if ($files.Count -eq 0) {
    throw "No supported audio or video files were found."
}

$duplicateBaseNames = $files | Group-Object BaseName | Where-Object { $_.Count -gt 1 }
if ($duplicateBaseNames) {
    $names = ($duplicateBaseNames | ForEach-Object Name) -join ", "
    throw "Input basenames must be unique for batch output: $names"
}

$OutDir = [System.IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$workRoot = Join-Path $OutDir (".vocal-separation-work-" + [guid]::NewGuid().ToString("N"))
$preparedAudioDir = Join-Path $workRoot "prepared-audio"
$stemsDir = Join-Path $workRoot "stems"
New-Item -ItemType Directory -Force -Path $preparedAudioDir, $stemsDir | Out-Null

$separatorWsl = "$AiModelsRootWsl/miniconda_envs/cosyvoice310/bin/audio-separator"
$resolvedModel = if ($Model -eq "Auto") {
    if ($Stem -eq "Vocals") { "MelBand" } else { "BSRoformer" }
} else {
    $Model
}

if ($resolvedModel -eq "MelBand") {
    $modelDirWsl = "$AiModelsRootWsl/UVR/models/roformer/vocal_melband"
    $modelFilename = "vocals_mel_band_roformer.ckpt"
}
else {
    $modelDirWsl = "$AiModelsRootWsl/UVR/models"
    $modelFilename = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
}
$completed = $false

try {
    $jobs = foreach ($file in $files) {
        $extension = $file.Extension.ToLowerInvariant()
        $kind = if ($videoExtensions -contains $extension) { "video" } else { "audio" }
        $prepared = $file.FullName

        if ($kind -eq "video") {
            $prepared = Join-Path $preparedAudioDir ($file.BaseName + ".wav")
            Invoke-WslCommand @(
                $FfmpegWsl, "-hide_banner", "-loglevel", "warning", "-y",
                "-i", (Convert-ToWslPath $file.FullName),
                "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
                (Convert-ToWslPath $prepared)
            )
        }

        [pscustomobject]@{
            Source = $file
            Kind = $kind
            Prepared = $prepared
            PreparedBase = ([System.IO.Path]::GetFileNameWithoutExtension($prepared)).TrimStart([char[]]".")
        }
    }

    $separatorArgs = @($separatorWsl)
    $separatorArgs += @($jobs | ForEach-Object { Convert-ToWslPath $_.Prepared })
    $separatorArgs += @(
        "--model_file_dir", $modelDirWsl,
        "--model_filename", $modelFilename,
        "--output_dir", (Convert-ToWslPath $stemsDir),
        "--output_format", "wav"
    )
    # BS-Roformer must emit both stems; its --single_stem mode can omit output.
    if ($resolvedModel -eq "MelBand") {
        $separatorArgs += @("--single_stem", $Stem)
    }
    if ($UseAutocast) {
        $separatorArgs += "--use_autocast"
    }

    Invoke-WslCommand $separatorArgs

    $stemPattern = if ($Stem -eq "Other") { "\((other|instrumental)\)" } else { "\(vocals\)" }
    $overwriteFlag = if ($Overwrite) { "-y" } else { "-n" }
    $outputs = @()

    foreach ($job in $jobs) {
        $stemFile = Get-ChildItem -LiteralPath $stemsDir -File |
            Where-Object {
                $_.BaseName.StartsWith($job.PreparedBase, [System.StringComparison]::OrdinalIgnoreCase) -and
                $_.Name -match $stemPattern
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1

        if (-not $stemFile) {
            throw "Could not locate the $Stem stem for $($job.Source.Name)"
        }

        $suffix = if ($Stem -eq "Other") { "BGM" } else { "Vocals" }
        $target = if ($job.Kind -eq "video") {
            Join-Path $OutDir ($job.Source.BaseName + ".mp4")
        }
        else {
            Join-Path $OutDir ($job.Source.BaseName + "_" + $suffix + ".wav")
        }

        if ((Test-Path -LiteralPath $target) -and (-not $Overwrite)) {
            throw "Output already exists (pass -Overwrite to replace it): $target"
        }

        if ($job.Kind -eq "audio") {
            Copy-Item -LiteralPath $stemFile.FullName -Destination $target -Force:$Overwrite
        }
        else {
            $muxArgs = @(
                $FfmpegWsl, "-hide_banner", "-loglevel", "warning", $overwriteFlag,
                "-i", (Convert-ToWslPath $job.Source.FullName),
                "-i", (Convert-ToWslPath $stemFile.FullName),
                "-map", "0:v:0", "-map", "1:a:0"
            )

            if ($Scale) {
                $muxArgs += @(
                    "-vf", ("scale=" + $Scale + ":flags=lanczos"),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-profile:v", "high", "-pix_fmt", "yuv420p"
                )
            }
            else {
                $muxArgs += @("-c:v", "copy")
            }

            $muxArgs += @(
                "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
                "-map_metadata", "0", "-movflags", "+faststart", "-shortest",
                (Convert-ToWslPath $target)
            )
            Invoke-WslCommand $muxArgs
        }

        $outputInfo = Get-Item -LiteralPath $target
        if ($outputInfo.Length -le 0) {
            throw "Output is empty: $target"
        }
        $outputs += $outputInfo
    }

    $completed = $true
    $outputs | Select-Object Name, Length, LastWriteTime, FullName
}
finally {
    if ($completed -and (Test-Path -LiteralPath $workRoot) -and (-not $KeepWork)) {
        Remove-Item -LiteralPath $workRoot -Recurse -Force
    }
}
