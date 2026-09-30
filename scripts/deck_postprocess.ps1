# Post-process the deck built by scripts/build_deck.py (requires Microsoft PowerPoint on Windows):
#   * replaces marker shapes named "SVG|<absolute svg path>" with the vector SVG itself (crisp icons/logos)
#   * saves the .pptx, exports a PDF (fonts embedded) and optional PNG previews
# usage: powershell -ExecutionPolicy Bypass -File scripts\deck_postprocess.ps1 -pptx <deck.pptx> [-pngdir <dir>]
param([string]$pptx, [string]$pngdir = "")
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open($pptx, $false, $false, $false)
$count = 0
foreach ($s in $pres.Slides) {
  $markers = @()
  foreach ($sh in $s.Shapes) { if ($sh.Name -like "SVG|*") { $markers += $sh } }
  foreach ($m in $markers) {
    $path = $m.Name.Substring(4)
    if (Test-Path $path) {
      $pic = $s.Shapes.AddPicture($path, 0, -1, $m.Left, $m.Top, $m.Width, $m.Height)
      $pic.Name = "icon"
      $count++
    }
    $m.Delete()
  }
}
$pres.Save()
$pdf = [System.IO.Path]::ChangeExtension($pptx, ".pdf")
$pres.SaveAs($pdf, 32)   # ppSaveAsPDF
if ($pngdir -ne "") {
  New-Item -ItemType Directory -Force $pngdir | Out-Null
  Get-ChildItem $pngdir -Filter *.png | Remove-Item -Force
  foreach ($s in $pres.Slides) { $s.Export((Join-Path $pngdir ("slide-{0:D2}.png" -f $s.SlideIndex)), "PNG", 1600, 900) }
}
$pres.Close()
$pp.Quit()
"icons placed: $count ; pdf: $pdf"
