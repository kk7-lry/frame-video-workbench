param([Parameter(Mandatory=$true)][string]$WavPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$rec = [System.Speech.Recognition.SpeechRecognitionEngine]::new()
$parts = [Collections.Generic.List[object]]::new()
try {
    $rec.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $rec.InitialSilenceTimeout = [TimeSpan]::FromSeconds(600)
    $rec.BabbleTimeout = [TimeSpan]::FromSeconds(600)
    $rec.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(600)
    $rec.SetInputToWaveFile($WavPath)
    while ($true) {
        $r = $rec.Recognize()
        if ($null -eq $r) { break }
        if ($r.Text) { $parts.Add(@{ text=$r.Text; time=$r.Audio.AudioPosition.TotalSeconds; confidence=[double]$r.Confidence }) }
    }
    [Console]::Write((ConvertTo-Json -InputObject @{ text=(($parts | ForEach-Object {$_.text}) -join "`n"); segments=@($parts.ToArray()) } -Compress -Depth 5))
} finally { $rec.Dispose() }
