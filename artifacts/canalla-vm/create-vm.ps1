# Create a disposable Windows 11 acceptance VM in VirtualBox.
# Free: the ISO is Microsoft's 90-day Enterprise Evaluation, no licence or cloud resource is bought.
$ErrorActionPreference = 'Stop'
$vbox = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
$iso = 'D:\canalla-vm\win11-eval.iso'
$vm = 'Canalla-127-Acceptance'
$disk = 'D:\canalla-vm\Canalla-127-Acceptance.vdi'
$password = 'Canalla-Test-127!'

if (-not (Test-Path $iso)) { throw "ISO missing: $iso" }
$size = (Get-Item $iso).Length
if ($size -lt 5000000000) { throw "ISO incomplete: $size bytes" }
Write-Output "ISO $size"

& $vbox list vms | Out-String | Write-Output
if ((& $vbox list vms) -match [regex]::Escape("`"$vm`"")) {
  Write-Output 'VM already exists, unregistering it'
  & $vbox unregistervm $vm --delete | Out-Null
}

& $vbox createvm --name $vm --ostype Windows11_64 --register --basefolder 'D:\canalla-vm' | Out-Null
& $vbox modifyvm $vm --memory 4096 --cpus 2 --vram 128 --graphicscontroller vboxsvga --firmware efi --tpm-type 2.0 --audio none --usb off --natpf1 'guestssh,tcp,,2222,,22' | Out-Null
& $vbox createmedium disk --filename $disk --size 40960 --format VDI | Out-Null
& $vbox storagectl $vm --name SATA --add sata --controller IntelAhci --portcount 4 | Out-Null
& $vbox storageattach $vm --storagectl SATA --port 0 --device 0 --type hdd --medium $disk | Out-Null
& $vbox storagectl $vm --name IDE --add ide | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\canalla-vm\share' | Out-Null
& $vbox sharedfolder add $vm --name payload --hostpath 'D:\canalla-vm\share' --automount | Out-Null

Write-Output 'Starting unattended installation (Windows 11 Enterprise Evaluation)'
& $vbox unattended install $vm `
  --iso=$iso `
  --user=canalla --password=$password --full-user-name='Canalla Test' `
  --locale=en_US --country=US --time-zone=UTC --hostname=canalla-test.local `
  --install-additions --additions-iso='C:\Program Files\Oracle\VirtualBox\VBoxGuestAdditions.iso' | Write-Output

# The installer ISO must be on the controller: a two-port controller left no slot next to the
# answer-file ISO, and the VM booted into the EFI shell instead of Windows setup.
$attached = & $vbox showvminfo $vm --machinereadable | Select-String -Pattern 'win11-eval.iso'
if (-not $attached) {
  & $vbox storageattach $vm --storagectl SATA --port 3 --device 0 --type dvddrive --medium $iso | Out-Null
  Write-Output 'installation ISO attached to SATA port 3'
} else {
  Write-Output 'installation ISO already attached by the unattended installer'
}

& $vbox startvm $vm --type headless | Write-Output

Write-Output 'installer started'
& $vbox showvminfo $vm --machinereadable | Select-String -Pattern '^VMState=' | Write-Output
