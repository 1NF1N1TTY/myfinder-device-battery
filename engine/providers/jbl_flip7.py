"""Read-only JBL Flip 7 BLE battery protocol.

Protocol evidence: ffrafat/JBLControl docs/PROTOCOL-FLIP7.md.
The Windows transport is hosted by the existing isolated Bluetooth PowerShell
worker; this module validates the returned notification frames.
"""
from __future__ import annotations

from typing import Iterable, Optional
from .base import DeviceStatus

FLIP7_PRODUCT_ID = 0x2107
JBL_COMPANY_ID = 0x0057
CONTROL_SERVICE = "65786365-6c70-6f69-6e74-2e636f6d0000"
NOTIFY_CHARACTERISTIC = "65786365-6c70-6f69-6e74-2e636f6d0001"
WRITE_CHARACTERISTIC = "65786365-6c70-6f69-6e74-2e636f6d0002"
BATTERY_REQUEST = bytes((0xAA, 0x11, 0x00))
INFO_REPLY = 0x12
BATTERY_TOKEN = 0x44


def parse_notifications(frames: Iterable[bytes]) -> Optional[tuple[int, bool]]:
    """Return (percent, charging) only from a complete AA 12 battery token frame."""
    pending = bytearray()
    for frame in frames:
        if not isinstance(frame, (bytes, bytearray, list, tuple)):
            continue
        try:
            chunk = bytes(frame)
        except (TypeError, ValueError):
            continue
        if len(chunk) > 256:
            continue
        pending.extend(chunk)
        if len(pending) > 512:
            del pending[:-512]
        while len(pending) >= 3:
            start = pending.find(b"\xAA")
            if start < 0:
                pending.clear()
                break
            if start:
                del pending[:start]
            if len(pending) < 3:
                break
            command, size = pending[1], pending[2]
            if size > 128:
                del pending[0]
                continue
            total = 3 + size
            if len(pending) < total:
                break
            packet = bytes(pending[:total])
            del pending[:total]
            if command != INFO_REPLY or size != 3:
                continue
            if packet[3] != 0x00 or packet[4] != BATTERY_TOKEN:
                continue
            raw = packet[5]
            percent = raw & 0x7F
            if percent > 100:
                continue
            return percent, bool(raw & 0x80)
    return None


def statuses_from_snapshot(rows, diagnostics=None):
    """Convert validated PowerShell BLE results into normal engine statuses."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        address = row.get("address")
        if type(address) is not int or not 0 < address < (1 << 48):
            continue
        raw_frames = row.get("frames")
        if not isinstance(raw_frames, list):
            continue
        frames = []
        for item in raw_frames[:32]:
            if not isinstance(item, str) or len(item) > 512 or len(item) % 2:
                continue
            try:
                frames.append(bytes.fromhex(item))
            except ValueError:
                continue
        battery = parse_notifications(frames)
        if battery is None:
            if diagnostics is not None:
                diagnostics.append("[JBL Flip 7] no valid battery notification")
            continue
        percent, charging = battery
        out.append(DeviceStatus(
            key=f"jbl-flip7:{address:012X}",
            name="JBL Flip 7",
            level=percent,
            charging=charging,
            online=True,
            source="jbl",
            kind="headset",
            via="bluetooth",
            source_quality="device_reply",
            connection_known=True,
            charging_known=True,
        ))
    return out


POWERSHELL = r"""
function Wait-JblWinRt($operation, $resultType, $timeoutMs) {
  try {
    $task = $script:asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    if (-not $task.Wait($timeoutMs)) { return $null }
    return $task.Result
  } catch { return $null }
}

function Get-JblFlip7Battery {
  $watcher = $null; $device = $null; $service = $null
  $tx = $null; $rx = $null; $notifyId = $null
  try {
    $script:tDevice = [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $script:tServices = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $script:tChars = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $script:tComm = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]

    $watcher = New-Object Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher
    $watcher.ScanningMode = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode]::Active
    $scanId = "JblFlip7Scan-" + [Guid]::NewGuid().ToString("N")
    Register-ObjectEvent -InputObject $watcher -EventName Received -SourceIdentifier $scanId | Out-Null
    $watcher.Start()
    $address = [UInt64]0
    $until = [DateTime]::UtcNow.AddSeconds(3)
    while ([DateTime]::UtcNow -lt $until -and $address -eq 0) {
      $evt = Wait-Event -SourceIdentifier $scanId -Timeout 0.2
      if (-not $evt) { continue }
      try {
        $advArgs = $evt.SourceArgs[1]
        foreach ($manufacturer in $advArgs.Advertisement.ManufacturerData) {
          if ($manufacturer.CompanyId -ne 0x0057) { continue }
          $reader = [Windows.Storage.Streams.DataReader]::FromBuffer($manufacturer.Data)
          $bytes = New-Object byte[] $reader.UnconsumedBufferLength
          $reader.ReadBytes($bytes)
          if ($bytes.Length -ge 2 -and $bytes[0] -eq 0x07 -and $bytes[1] -eq 0x21) {
            $address = [UInt64]$advArgs.BluetoothAddress
            break
          }
        }
      } finally { Remove-Event -EventIdentifier $evt.EventIdentifier -ErrorAction SilentlyContinue }
    }
    $watcher.Stop()
    Unregister-Event -SourceIdentifier $scanId -ErrorAction SilentlyContinue
    if ($address -eq 0) { return @() }

    $device = Wait-JblWinRt ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync($address)) $script:tDevice 2500
    if (-not $device) { return @() }
    $serviceId = [Guid]::Parse("65786365-6c70-6f69-6e74-2e636f6d0000")
    $services = Wait-JblWinRt ($device.GetGattServicesForUuidAsync($serviceId, [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) $script:tServices 2500
    if (-not $services -or "$($services.Status)" -ne "Success" -or $services.Services.Count -eq 0) { return @() }
    $service = $services.Services[0]
    $txId = [Guid]::Parse("65786365-6c70-6f69-6e74-2e636f6d0002")
    $rxId = [Guid]::Parse("65786365-6c70-6f69-6e74-2e636f6d0001")
    $txResult = Wait-JblWinRt ($service.GetCharacteristicsForUuidAsync($txId, [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) $script:tChars 2000
    $rxResult = Wait-JblWinRt ($service.GetCharacteristicsForUuidAsync($rxId, [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) $script:tChars 2000
    if (-not $txResult -or -not $rxResult -or $txResult.Characteristics.Count -eq 0 -or $rxResult.Characteristics.Count -eq 0) { return @() }
    $tx = $txResult.Characteristics[0]; $rx = $rxResult.Characteristics[0]

    $notifyId = "JblFlip7Notify-" + [Guid]::NewGuid().ToString("N")
    Register-ObjectEvent -InputObject $rx -EventName ValueChanged -SourceIdentifier $notifyId | Out-Null
    $cccd = Wait-JblWinRt ($rx.WriteClientCharacteristicConfigurationDescriptorAsync([Windows.Devices.Bluetooth.GenericAttributeProfile.GattClientCharacteristicConfigurationDescriptorValue]::Notify)) $script:tComm 2000
    if ("$cccd" -ne "Success") { return @() }

    $writer = New-Object Windows.Storage.Streams.DataWriter
    $writer.WriteBytes([byte[]](0xAA, 0x11, 0x00))
    $buffer = $writer.DetachBuffer()
    $write = Wait-JblWinRt ($tx.WriteValueAsync($buffer, [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption]::WriteWithResponse)) $script:tComm 2000
    if ("$write" -ne "Success") { return @() }

    $frames = New-Object System.Collections.Generic.List[string]
    $until = [DateTime]::UtcNow.AddSeconds(2)
    while ([DateTime]::UtcNow -lt $until -and $frames.Count -lt 32) {
      $evt = Wait-Event -SourceIdentifier $notifyId -Timeout 0.2
      if (-not $evt) { continue }
      try {
        $value = $evt.SourceArgs[1].CharacteristicValue
        $reader = [Windows.Storage.Streams.DataReader]::FromBuffer($value)
        $bytes = New-Object byte[] $reader.UnconsumedBufferLength
        $reader.ReadBytes($bytes)
        if ($bytes.Length -gt 0) { $frames.Add([BitConverter]::ToString($bytes).Replace("-", "")) }
      } finally { Remove-Event -EventIdentifier $evt.EventIdentifier -ErrorAction SilentlyContinue }
    }
    if ($frames.Count -eq 0) { return @() }
    return [pscustomobject]@{ address = $address; frames = @($frames) }
  } catch {
    return @()
  } finally {
    if ($watcher) { try { $watcher.Stop() } catch { } }
    if ($notifyId) { Unregister-Event -SourceIdentifier $notifyId -ErrorAction SilentlyContinue }
    if ($service) { try { $service.Dispose() } catch { } }
    if ($device) { try { $device.Dispose() } catch { } }
  }
}
"""
