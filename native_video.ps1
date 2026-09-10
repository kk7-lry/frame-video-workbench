param([Parameter(Mandatory=$true)][string]$MediaPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$runtimeAssembly = [Reflection.Assembly]::Load('System.Runtime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a')
$interopAssembly = [Reflection.Assembly]::Load('System.Runtime.InteropServices.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a')
$compiler = [Microsoft.CSharp.CSharpCodeProvider]::new()
$parameters = [System.CodeDom.Compiler.CompilerParameters]::new()
$parameters.GenerateInMemory = $true
foreach ($reference in @('System.Runtime.WindowsRuntime.dll', $runtimeAssembly.Location, $interopAssembly.Location)) {
    $parameters.ReferencedAssemblies.Add($reference) | Out-Null
}
foreach ($name in @('Windows.Media', 'Windows.Foundation', 'Windows.Storage', 'Windows.Graphics')) {
    $parameters.ReferencedAssemblies.Add((Join-Path $env:SystemRoot "System32\WinMetadata\$name.winmd")) | Out-Null
}
$compiled = $compiler.CompileAssemblyFromSource($parameters, @'
public static class FrameVideoProbe {
    public static void Add(Windows.Media.Editing.MediaComposition composition, Windows.Media.Editing.MediaClip clip) {
        composition.Clips.Add(clip);
    }
}
'@)
if ($compiled.Errors.HasErrors) { throw ($compiled.Errors | Out-String) }
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Media.Editing.MediaClip, Windows.Media.Editing, ContentType=WindowsRuntime]
$null = [Windows.Media.Editing.MediaComposition, Windows.Media.Editing, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.ImageStream, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$asyncMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($Operation, [Type]$ResultType) {
    $task = $asyncMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($MediaPath)) ([Windows.Storage.StorageFile])
    $clip = Await ([Windows.Media.Editing.MediaClip]::CreateFromFileAsync($file)) ([Windows.Media.Editing.MediaClip])
    $properties = $clip.GetVideoEncodingProperties()
    $duration = $clip.OriginalDuration.TotalSeconds
    if ($properties.Width -eq 0 -or $properties.Height -eq 0 -or $duration -le 0) { throw 'No valid video track.' }
    $composition = [Windows.Media.Editing.MediaComposition]::new()
    $compiled.CompiledAssembly.GetType('FrameVideoProbe').GetMethod('Add').Invoke($null, @($composition, $clip))
    foreach ($seconds in @(0.0, [Math]::Max(0, $duration - 0.2))) {
        $image = Await ($composition.GetThumbnailAsync([TimeSpan]::FromSeconds($seconds), 160, 0, [Windows.Media.Editing.VideoFramePrecision]::NearestFrame)) ([Windows.Graphics.Imaging.ImageStream])
        $bitmap = $null
        try {
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($image)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            if ($bitmap.PixelWidth -eq 0 -or $bitmap.PixelHeight -eq 0) { throw 'Frame could not be decoded.' }
        } finally { if ($bitmap) { $bitmap.Dispose() }; $image.Dispose() }
    }
    [Console]::Write((@{ok=$true; width=$properties.Width; height=$properties.Height; duration=$duration; decodedFrames=2} | ConvertTo-Json -Compress))
} catch {
    [Console]::Write((@{ok=$false; reason='media_decode_failed'} | ConvertTo-Json -Compress))
}
