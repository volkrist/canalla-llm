import { invoke, isTauri } from "@tauri-apps/api/core";

export interface DeviceStatus {
  paired: boolean;
  online: boolean;
  device_id?: string;
  display_name?: string;
  storage?: string;
}

export async function readDeviceStatus(): Promise<DeviceStatus> {
  if (!isTauri()) return { paired: false, online: false };
  return invoke<DeviceStatus>("device_status");
}

export async function pairLocalDevice(
  backendUrl: string,
  token: string,
  displayName: string,
) {
  if (!isTauri()) return { paired: false, online: false };
  return invoke<DeviceStatus>("pair_device", {
    backendUrl,
    token,
    displayName,
  });
}

export async function runHostJobs(
  backendUrl: string,
  token: string,
  roots: string[],
) {
  if (!isTauri()) return { completed: 0 };
  return invoke<{ completed: number }>("execute_host_jobs", {
    backendUrl,
    token,
    roots,
  });
}
