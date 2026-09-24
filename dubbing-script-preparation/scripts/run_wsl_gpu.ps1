param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArguments,
    [string]$Distro = "Ubuntu",
    [string]$Python = "/home/zhaoyi/miniconda/envs/stable-ai/bin/python"
)

$ErrorActionPreference = "Stop"

function Convert-ToWslPath([string]$Value) {
    if ($Value -match '^([^=]+=)([A-Za-z]):[\\/](.*)$') {
        $prefix = $Matches[1]
        $drive = $Matches[2].ToLowerInvariant()
        $tail = $Matches[3] -replace '\\', '/'
        return "$prefix/mnt/$drive/$tail"
    }
    if ($Value -match '^([A-Za-z]):[\\/](.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $tail = $Matches[2] -replace '\\', '/'
        return "/mnt/$drive/$tail"
    }
    return $Value
}

$wslScript = Convert-ToWslPath $Script
$converted = @($ScriptArguments | ForEach-Object { Convert-ToWslPath $_ })
& wsl.exe -d $Distro -- env TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 $Python $wslScript @converted
exit $LASTEXITCODE
