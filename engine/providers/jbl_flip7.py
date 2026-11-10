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
            # This reader only accepts the fixed three-byte battery-info payload.
            # Reject unrelated headers immediately; waiting for their advertised
            # length could let a corrupt prefix hide a valid AA 12 battery frame.
            if command != INFO_REPLY or size != 3:
                del pending[0]
                continue
            total = 3 + size
            if len(pending) < total:
                break
            packet = bytes(pending[:total])
            del pending[:total]
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

function New-JblWinRtHandler($sourceType, $argsType, $queue) {
  $generic = [Windows.Foundation.TypedEventHandler``2, Windows, ContentType = WindowsRuntime]
  $delegateType = $generic.MakeGenericType([type[]]@($sourceType, $argsType))
  $invoke = $delegateType.GetMethod("Invoke")
  $parameters = [System.Linq.Expressions.ParameterExpression[]]@(
    $invoke.GetParameters() | ForEach-Object {
      [System.Linq.Expressions.Expression]::Parameter($_.ParameterType, $_.Name)
    })
  $enqueue = $queue.GetType().GetMethod("Enqueue")
  $body = [System.Linq.Expressions.Expression]::Call(
    [System.Linq.Expressions.Expression]::Constant($queue),
    $enqueue,
    [System.Linq.Expressions.Expression]::Convert($parameters[1], [object]))
  return [System.Linq.Expressions.Expression]::Lambda($delegateType, $body, $parameters).Compile()
}

function Add-JblFlip7Diag($message) {
  $script:jblFlip7Diag += [string]$message
}

function Get-JblFlip7Battery {
  $watcher = $null; $device = $null; $service = $null
  $tx = $null; $rx = $null; $scanToken = $null; $notifyToken = $null
  $scanHandler = $null; $notifyHandler = $null
  $scanQueue = $null; $notifyQueue = $null
  $script:jblFlip7Diag = @()
  try {
    $script:tDevice = [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $script:tServices = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $script:tChars = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $script:tComm = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]

    $script:tWatcher = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher, Windows, ContentType = WindowsRuntime]
    $script:tAdvArgs = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementReceivedEventArgs, Windows, ContentType = WindowsRuntime]
    $watcher = New-Object Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher
    $watcher.ScanningMode = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode]::Active
    $scanQueue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[object]'
    $scanHandler = New-JblWinRtHandler $script:tWatcher $script:tAdvArgs $scanQueue
    $scanToken = $watcher.add_Received($scanHandler)
    Add-JblFlip7Diag "BLE scan started"
    $watcher.Start()
    $address = [UInt64]0
    $until = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $until -and $address -eq 0) {
      $advArgs = $null
      if (-not $scanQueue.TryDequeue([ref]$advArgs)) { Start-Sleep -Milliseconds 50; continue }
      foreach ($manufacturer in $advArgs.Advertisement.ManufacturerData) {
        if ($manufacturer.CompanyId -ne 0x0057) { continue }
        $reader = [Windows.Storage.Streams.DataReader]::FromBuffer($manufacturer.Data)
        try {
          $bytes = New-Object byte[] $reader.UnconsumedBufferLength
          $reader.ReadBytes($bytes)
          if ($bytes.Length -ge 2 -and $bytes[0] -eq 0x07 -and $bytes[1] -eq 0x21) {
            $address = [UInt64]$advArgs.BluetoothAddress
            Add-JblFlip7Diag ("matching advertisement " + ('{0:X12}' -f $address))
            break
          }
        } finally { $reader.Dispose() }
      }
    }
    $watcher.Stop()
    if ($scanToken) { $watcher.remove_Received($scanToken); $scanToken = $null }
    if ($address -eq 0) { Add-JblFlip7Diag "no matching 0x0057 / 07-21 advertisement within 5 seconds"; return @() }

    $device = Wait-JblWinRt ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync($address)) $script:tDevice 4000
    if (-not $device) { Add-JblFlip7Diag "BluetoothLEDevice creation failed or timed out"; return @() }
    Add-JblFlip7Diag "BluetoothLEDevice created"
    $serviceId = [Guid]::Parse("65786365-6c70-6f69-6e74-2e636f6d0000")
    $services = Wait-JblWinRt ($device.GetGattServicesForUuidAsync($serviceId, [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) $script:tServices 2500
    if (-not $services -or "$($services.Status)" -ne "Success" -or $services.Services.Count -eq 0) { Add-JblFlip7Diag ("GATT service lookup failed: " + $(if($services){"$($services.Status)"}else{"no result"})); return @() }
    $service = $services.Services[0]
    Add-JblFlip7Diag "control GATT service found"
    $txId = [Guid]::Parse("65786365-6c70-6f69-6e74-2e636f6d0002")
    $rxId = [Guid]::Parse("65786365-6c70-6f69-6e74-2e636f6d0001")
    $txResult = Wait-JblWinRt ($service.GetCharacteristicsForUuidAsync($txId, [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) $script:tChars 2000
    $rxResult = Wait-JblWinRt ($service.GetCharacteristicsForUuidAsync($rxId, [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) $script:tChars 2000
    if (-not $txResult -or -not $rxResult -or $txResult.Characteristics.Count -eq 0 -or $rxResult.Characteristics.Count -eq 0) { Add-JblFlip7Diag "GATT TX/RX characteristic lookup failed"; return @() }
    $tx = $txResult.Characteristics[0]; $rx = $rxResult.Characteristics[0]
    $txProperties = [int]$tx.CharacteristicProperties
    $rxProperties = [int]$rx.CharacteristicProperties
    if (($txProperties -band 0x08) -eq 0) { Add-JblFlip7Diag "GATT TX characteristic does not support WriteWithResponse"; return @() }
    if (($rxProperties -band 0x10) -eq 0) { Add-JblFlip7Diag "GATT RX characteristic does not support Notify"; return @() }
    Add-JblFlip7Diag "TX WriteWithResponse and RX Notify properties confirmed"

    $script:tGattChar = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic, Windows, ContentType = WindowsRuntime]
    $script:tGattValueArgs = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattValueChangedEventArgs, Windows, ContentType = WindowsRuntime]
    $notifyQueue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[object]'
    $notifyHandler = New-JblWinRtHandler $script:tGattChar $script:tGattValueArgs $notifyQueue
    $notifyToken = $rx.add_ValueChanged($notifyHandler)
    $cccd = Wait-JblWinRt ($rx.WriteClientCharacteristicConfigurationDescriptorAsync([Windows.Devices.Bluetooth.GenericAttributeProfile.GattClientCharacteristicConfigurationDescriptorValue]::Notify)) $script:tComm 2000
    if ("$cccd" -ne "Success") { Add-JblFlip7Diag ("CCCD subscription failed: " + $cccd); return @() }
    Add-JblFlip7Diag "notification subscription succeeded"
    # Drop any notification queued during CCCD setup; only post-query frames
    # may satisfy this battery request.
    $stale = $null
    while ($notifyQueue.TryDequeue([ref]$stale)) { }

    $writer = New-Object Windows.Storage.Streams.DataWriter
    $writer.WriteBytes([byte[]](0xAA, 0x11, 0x00))
    $buffer = $writer.DetachBuffer()
    $writer.Dispose()
    $write = Wait-JblWinRt ($tx.WriteValueAsync($buffer, [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption]::WriteWithResponse)) $script:tComm 2000
    if ("$write" -ne "Success") { Add-JblFlip7Diag ("battery query write failed: " + $write); return @() }
    Add-JblFlip7Diag "battery query AA 11 00 written"

    $frames = New-Object System.Collections.Generic.List[string]
    $until = [DateTime]::UtcNow.AddSeconds(3)
    while ([DateTime]::UtcNow -lt $until -and $frames.Count -lt 32) {
      $valueArgs = $null
      if (-not $notifyQueue.TryDequeue([ref]$valueArgs)) { Start-Sleep -Milliseconds 50; continue }
      $value = $valueArgs.CharacteristicValue
      $reader = [Windows.Storage.Streams.DataReader]::FromBuffer($value)
      try {
        $bytes = New-Object byte[] $reader.UnconsumedBufferLength
        $reader.ReadBytes($bytes)
        if ($bytes.Length -gt 0) { $frames.Add([BitConverter]::ToString($bytes).Replace("-", "")) }
      } finally { $reader.Dispose() }
    }
    if ($frames.Count -eq 0) { Add-JblFlip7Diag "no GATT notification received within 3 seconds"; return @() }
    Add-JblFlip7Diag ("received " + $frames.Count + " GATT notification frame(s)")
    return [pscustomobject]@{ address = $address; frames = @($frames) }
  } catch {
    Add-JblFlip7Diag ("exception: " + $_.Exception.Message)
    return @()
  } finally {
    if ($watcher) { try { $watcher.Stop() } catch { } }
    if ($watcher -and $scanToken) { try { $watcher.remove_Received($scanToken) } catch { } }
    if ($rx -and $notifyToken) { try { $rx.remove_ValueChanged($notifyToken) } catch { } }
    if ($service) { try { $service.Dispose() } catch { } }
    if ($device) { try { $device.Dispose() } catch { } }
  }
}
"""
