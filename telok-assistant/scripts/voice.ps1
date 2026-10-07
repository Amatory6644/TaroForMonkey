param([string]$TextPath, [string]$OutputPath, [string]$Locale = "ru-RU")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$voiceEngine = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $available = @($voiceEngine.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $Locale })
    if ($available.Count -eq 0) { throw "No installed voice for requested locale." }
    $voiceEngine.SelectVoice($available[0].VoiceInfo.Name)
    $voiceEngine.SetOutputToWaveFile($OutputPath)
    $voiceEngine.Speak([System.IO.File]::ReadAllText($TextPath))
} finally {
    $voiceEngine.Dispose()
}
