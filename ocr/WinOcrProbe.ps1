# WinOcrProbe.ps1 -- ASCII only (PS 5.1 reads .ps1 as GBK; Chinese would break it)
# Purpose: test whether Windows.Media.Ocr can be called from a PLAIN PowerShell process
#          (i.e. without MSIX package identity), on an EXISTING png on disk.
# No screen access. No admin. Read-only.

param(
    [Parameter(Mandatory=$true)][string]$Path
)

$ErrorActionPreference = 'Stop'
Write-Output "=== Windows.Media.Ocr probe ==="
Write-Output ("PSVersion      : " + $PSVersionTable.PSVersion.ToString())
Write-Output ("Image          : " + $Path)
if (-not (Test-Path $Path)) { Write-Output "IMAGE NOT FOUND"; exit 2 }
$fi = Get-Item $Path
Write-Output ("Image size     : {0} bytes" -f $fi.Length)

# --- does the process have MSIX package identity? ---
try {
    [void][Windows.ApplicationModel.AppInfo,Windows.ApplicationModel,ContentType=WindowsRuntime]
    Write-Output "AppInfo type   : reachable"
} catch {
    Write-Output ("AppInfo type   : NOT reachable -> " + $_.Exception.Message)
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    Write-Output "WinRT interop  : System.Runtime.WindowsRuntime loaded"
} catch {
    Write-Output ("WinRT interop  : FAILED -> " + $_.Exception.Message)
    exit 3
}

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    [void]$netTask.Wait(-1)
    $netTask.Result
}

# --- OCR engine ---
# NOTE: WinRT types must be projected explicitly before use, otherwise
# "Unable to find type [Windows.Media.Ocr.OcrEngine]" (probe run #1 hit exactly this).
foreach ($t in @(
    '[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]',
    '[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]'
)) {
    try { [void](Invoke-Expression $t); Write-Output ("projected      : " + $t) }
    catch { Write-Output ("project FAILED : " + $t + " -> " + $_.Exception.Message) }
}

try {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    Write-Output ("Engine(userLang): " + ($(if ($null -eq $engine) { "NULL" } else { "OK" })))
} catch {
    Write-Output ("Engine(userLang): EXCEPTION -> " + $_.Exception.Message)
    $engine = $null
}

if ($null -eq $engine) {
    foreach ($lang in @('zh-Hans-CN','zh-CN','en-US')) {
        try {
            $l = New-Object Windows.Globalization.Language $lang
            $e2 = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($l)
            Write-Output ("Engine(" + $lang + "): " + ($(if ($null -eq $e2) { "NULL" } else { "OK" })))
            if ($null -ne $e2) { $engine = $e2; break }
        } catch {
            Write-Output ("Engine(" + $lang + "): EXCEPTION -> " + $_.Exception.Message)
        }
    }
}

try {
    Write-Output ("AvailableLangs : " + (([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }) -join ', '))
    Write-Output ("MaxImageDim    : " + [Windows.Media.Ocr.OcrEngine]::MaxImageDimension)
} catch {
    Write-Output ("Langs/MaxDim   : EXCEPTION -> " + $_.Exception.Message)
}

if ($null -eq $engine) {
    Write-Output "RESULT: engine could not be created"
    exit 4
}

# --- decode image ---
# Run #2 used StorageFile.GetFileFromPathAsync -> "One or more errors occurred."
# Run #3 avoids the file API entirely: read bytes ourselves, feed an in-memory stream.
foreach ($t in @(
    '[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]',
    '[Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]',
    '[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]'
)) {
    try { [void](Invoke-Expression $t) } catch { Write-Output ("project FAILED : " + $t + " -> " + $_.Exception.Message) }
}

function Show-Err($e, $tag) {
    Write-Output ("[" + $tag + "] " + $e.Exception.Message)
    $inner = $e.Exception
    $depth = 0
    while ($inner.InnerException -and $depth -lt 4) {
        $inner = $inner.InnerException
        $depth++
        Write-Output ("   inner(" + $depth + "): " + $inner.GetType().Name + " -> " + $inner.Message)
        if ($inner.HResult) { Write-Output ("      HRESULT: 0x" + ('{0:X8}' -f $inner.HResult)) }
    }
}

try {
    $full  = (Resolve-Path $Path).Path
    $bytes = [System.IO.File]::ReadAllBytes($full)
    Write-Output ("Bytes read     : " + $bytes.Length)

    $ms = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $dw = New-Object Windows.Storage.Streams.DataWriter($ms)
    $dw.WriteBytes($bytes)
    [void](Await ($dw.StoreAsync()) ([uint32]))
    [void](Await ($dw.FlushAsync()) ([bool]))
    [void]$dw.DetachStream()
    $ms.Seek(0)
    Write-Output ("Stream size    : " + $ms.Size)

    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ms)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    Write-Output ("Bitmap         : {0}x{1}" -f $bitmap.PixelWidth, $bitmap.PixelHeight)
} catch {
    Show-Err $_ "Decode"
    Write-Output "RESULT: image decode failed"
    exit 5
}

# --- recognize ---
try {
    $res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $txt = $res.Text
    Write-Output ("OCR text       : [" + $txt + "]")
    Write-Output ("Line count     : " + $res.Lines.Count)
    foreach ($ln in $res.Lines) {
        $w = ($ln.Words | ForEach-Object { $_.Text }) -join '|'
        Write-Output ("  line: [" + $ln.Text + "]  words=" + $ln.Words.Count + " -> " + $w)
    }
    Write-Output "RESULT: OCR CALL SUCCEEDED FROM A PLAIN PROCESS"
    exit 0
} catch {
    Write-Output ("Recognize FAILED: " + $_.Exception.Message)
    Write-Output ("  HRESULT: 0x" + ('{0:X8}' -f $_.Exception.HResult))
    Write-Output "RESULT: OCR call failed"
    exit 6
}
