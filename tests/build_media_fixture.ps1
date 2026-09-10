param(
    [Parameter(Mandatory=$true)][string]$ImagePath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$compiler = [Microsoft.CSharp.CSharpCodeProvider]::new()
$parameters = [System.CodeDom.Compiler.CompilerParameters]::new()
$parameters.GenerateInMemory = $true
$parameters.ReferencedAssemblies.Add('System.Runtime.WindowsRuntime.dll') | Out-Null
$parameters.ReferencedAssemblies.Add(([Reflection.Assembly]::Load('System.Runtime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a')).Location) | Out-Null
$parameters.ReferencedAssemblies.Add(([Reflection.Assembly]::Load('System.Runtime.InteropServices.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a')).Location) | Out-Null
$parameters.ReferencedAssemblies.Add((Join-Path $env:SystemRoot 'System32\WinMetadata\Windows.Media.winmd')) | Out-Null
$parameters.ReferencedAssemblies.Add((Join-Path $env:SystemRoot 'System32\WinMetadata\Windows.Foundation.winmd')) | Out-Null
$compiled = $compiler.CompileAssemblyFromSource($parameters, @'
public static class FrameFixtureClips {
    public static void Add(Windows.Media.Editing.MediaComposition composition, Windows.Media.Editing.MediaClip clip) {
        composition.Clips.Add(clip);
    }
}
'@)
if ($compiled.Errors.HasErrors) { throw ($compiled.Errors | Out-String) }
$fixtureType = $compiled.CompiledAssembly.GetType('FrameFixtureClips')
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFolder, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Media.Editing.MediaClip, Windows.Media.Editing, ContentType=WindowsRuntime]
$null = [Windows.Media.Editing.MediaComposition, Windows.Media.Editing, ContentType=WindowsRuntime]
$null = [Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType=WindowsRuntime]
$null = [Windows.Media.Transcoding.TranscodeFailureReason, Windows.Media.Transcoding, ContentType=WindowsRuntime]
$asyncMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
$progressMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperationWithProgress`2'
} | Select-Object -First 1
function Await($Operation, [Type]$ResultType) {
    $task = $asyncMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
$image = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$clip = Await ([Windows.Media.Editing.MediaClip]::CreateFromImageFileAsync($image, [TimeSpan]::FromSeconds(2))) ([Windows.Media.Editing.MediaClip])
$composition = [Windows.Media.Editing.MediaComposition]::new()
$fixtureType.GetMethod('Add').Invoke($null, @($composition, $clip))
$folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync((Split-Path $OutputPath))) ([Windows.Storage.StorageFolder])
$output = Await ($folder.CreateFileAsync((Split-Path $OutputPath -Leaf), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
$profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp4([Windows.Media.MediaProperties.VideoEncodingQuality]::Vga)
$operation = $composition.RenderToFileAsync($output, [Windows.Media.Editing.MediaTrimmingPreference]::Precise, $profile)
$task = $progressMethod.MakeGenericMethod([Windows.Media.Transcoding.TranscodeFailureReason], [double]).Invoke($null, @($operation))
$result = $task.GetAwaiter().GetResult()
if ($result -ne [Windows.Media.Transcoding.TranscodeFailureReason]::None) { throw "Video render failed: $result" }
Get-Item -LiteralPath $OutputPath | Select-Object FullName,Length
