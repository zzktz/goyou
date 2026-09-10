; Stop processes from older GoYou/ProxySwitch installations before NSIS
; replaces sing-box.exe. A forced taskkill is intentional here: this hook
; runs while the old application may still be open or may have been left in
; the tray, and Windows otherwise keeps the executable locked.
!macro NSIS_HOOK_PREINSTALL
  nsExec::ExecToLog 'taskkill.exe /IM sing-box.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM GoYou.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM goyou.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM ProxySwitch.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM proxyswitch.exe /T /F'
  Sleep 1000
!macroend

; Apply the same cleanup before uninstalling so an active proxy cannot leave
; the old sing-box binary or configuration behind as a locked file.
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog 'taskkill.exe /IM sing-box.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM GoYou.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM goyou.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM ProxySwitch.exe /T /F'
  nsExec::ExecToLog 'taskkill.exe /IM proxyswitch.exe /T /F'
  Sleep 1000
!macroend
