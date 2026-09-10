$ErrorActionPreference='Stop'
$projectRoot=$PSScriptRoot
$destination=Join-Path (Split-Path $projectRoot) 'Frame-Workbench-v0.3.0.zip'
$files=@('index.html','app.js','style.css','favicon.svg','server.py','link_resolver.py','platform_auth.py','native_ocr.ps1','native_speech.ps1','start.ps1','Start-Frame.cmd','Restart-Frame.cmd','README.md','THIRD_PARTY.md','build-icons.cjs','package.ps1','tests/test_server.py','tests/test_links.py','tests/check_frontend.cjs','tests/fixtures/chinese.png')
$files += @('ACCEPTANCE.md','native_video.ps1','tests/fixtures/sample.mp4','tests/build_media_fixture.ps1')
$files += @('tests/test_startup.py','tests/test_frontend.cjs')
$files += @('public_access.py','media_tools.py','Dockerfile','.dockerignore','.gitignore','requirements.txt','render.yaml','DEPLOY.md','tests/test_public.py','tests/test_media_tools.py')
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stream=[IO.File]::Open($destination,[IO.FileMode]::Create)
$zip=[IO.Compression.ZipArchive]::new($stream,[IO.Compression.ZipArchiveMode]::Create)
try {
    foreach($relative in $files){
        $source=Join-Path $projectRoot $relative
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,$source,('Frame-Workbench/'+$relative),[IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {$zip.Dispose();$stream.Dispose()}
Get-Item -LiteralPath $destination | Select-Object FullName,Length
