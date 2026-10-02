// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
mod device;
mod firmware;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            device::list_readers,
            device::card_status,
            device::transmit_hsm,
            device::get_rtc_time,
            device::set_rtc_time,
            device::login,
            device::change_pin,
            device::unblock_pin,
            device::init_device,
            device::dkek_status,
            device::dkek_import_share,
            device::dkek_setup_domain,
            device::wrap_key,
            device::unwrap_key,
            device::get_version,
            device::get_pin_retries,
            device::get_sopin_retries,
            device::get_platform_info,
            device::get_flash_info,
            device::get_secure_info,
            device::reboot_device,
            device::get_serial,
            device::list_keys,
            device::list_certs,
            device::delete_cert,
            device::import_cert,
            device::export_cert,
            device::export_csr,
            device::export_pubkey,
            device::key_details,
            device::set_label,
            device::gen_aes,
            device::gen_rsa,
            device::gen_ec,
            device::delete_key,
            device::set_dynops,
            firmware::parse_uf2_file,
            firmware::verify_uf2_hash,
            firmware::find_bootsel_drive,
            firmware::flash_uf2
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
