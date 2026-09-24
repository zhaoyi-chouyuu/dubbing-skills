param(
  [int]$Port = 8000,
  [string]$Distro = "Ubuntu",
  [string]$CodeDir = "E:\\AI_Models\\CosyVoice_code",
  [string]$ModelDir = "E:\\AI_Models\\Fun-CosyVoice3-0.5B-2512",
  [string]$PythonWsl = "/mnt/e/AI_Models/miniconda_envs/cosyvoice310/bin/python3",
  [switch]$Background
)

function Convert-ToWslPath([string]$p) {
  if ($p -match "^[a-zA-Z]:\\") {
    $drive = $p.Substring(0,1).ToLower()
    $rest = $p.Substring(2) -replace "\\","/"
    return "/mnt/$drive$rest"
  }
  return ($p -replace "\\","/")
}

function Escape-BashSQ([string]$s) {
  return ($s -replace "'", "'\"'\"''")
}

$codeWsl = Convert-ToWslPath $CodeDir
$modelWsl = Convert-ToWslPath $ModelDir

$py = Escape-BashSQ $PythonWsl
$code = Escape-BashSQ $codeWsl
$model = Escape-BashSQ $modelWsl
$p = [int]$Port

$cmd = "set -e; cd '$code'; '$py' -u webui.py --port $p --model_dir '$model'"

if ($Background) {
  Start-Process -FilePath "wsl.exe" -ArgumentList @("-d",$Distro,"bash","-lc",$cmd) | Out-Null
  Write-Host "[*] Started in background. Port: $Port"
} else {
  wsl -d $Distro bash -lc $cmd
}

