打包命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package_windows.ps1
```

说明

- 已统一使用 `Nuitka` 打包。
- 首次打包会自动下载编译器，耗时通常会比后续构建更久。
- 打包时会额外复制 `bin\ffmpeg.exe` 和 `bin\yt-dlp.exe` 到最终发布目录。
