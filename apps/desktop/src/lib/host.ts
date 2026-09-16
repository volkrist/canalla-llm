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

export async function forgetLocalDevice(backendUrl: string, token: string) {
  if (!isTauri()) return { paired: false, online: false };
  return invoke<DeviceStatus>("forget_device", { backendUrl, token });
}

export async function rotateDeviceCredential(
  backendUrl: string,
  token: string,
) {
  if (!isTauri()) return { paired: false, online: false };
  return invoke<DeviceStatus>("rotate_device_credential", {
    backendUrl,
    token,
  });
}

export async function storeUserCredential(name: string, secret: string) {
  if (!isTauri()) return { stored: false };
  return invoke<{ reference: string; stored: boolean }>(
    "store_user_credential",
    {
      name,
      secret,
    },
  );
}

export async function listUserCredentials() {
  if (!isTauri()) return [] as string[];
  return invoke<string[]>("list_user_credentials");
}

export async function deleteUserCredential(name: string) {
  if (!isTauri()) return { deleted: false };
  return invoke<{ reference: string; deleted: boolean }>(
    "delete_user_credential",
    { name },
  );
}
