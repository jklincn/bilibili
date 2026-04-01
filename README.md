# 哔哩哔哩（bilibili）视频下载器

本项目是 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的图形化界面封装，旨在为 Windows 平台上的非技术用户提供更简便的操作体验。

## 使用

点击[此处]()下载 7z 压缩包，解压至任意位置，双击解压目录中 bilibili.exe 即可运行。

## 开发者指南

### 页面布局

```
MainWindow
└─ central (CentralCanvas)
   ├─ window_surface (WindowSurface)
   │  └─ outer_layout
   │     ├─ title_bar
   │     └─ body_layout
   │        ├─ content_shell
   │        │  └─ content_layout
   │        │     ├─ hero_card
   │        │     ├─ info_card
   │        │     └─ controls_card
   │        └─ stretch_spacer
   └─ log_overlay
```

### 打包

需手动下载 ffmpeg.exe 以及 yt-dlp.exe 文件至 bin 目录下。

Windows 下打包命令

```
powershell -ExecutionPolicy Bypass -File scripts\package_windows.ps1
```
