import os
import sys
import json
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from datetime import datetime
from pathlib import Path
import re
from abc import ABC, abstractmethod
from enum import Enum

# 路径配置
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent.absolute()
else:
    BASE_DIR = Path(__file__).parent.absolute()
CONFIG_FILE = BASE_DIR / "config.json"
DOWNLOAD_DIR = BASE_DIR / "b站订阅"
LOG_FILE = BASE_DIR / "download.log"


def find_tool(name: str) -> str:
    """
    查找外部工具的可执行文件路径。
    优先级: exe同级目录(BASE_DIR) > 系统PATH
    返回工具名称（如果在PATH中）或完整路径。
    """
    exe_name = name if name.endswith('.exe') else name + '.exe' if sys.platform == 'win32' else name

    local_path = BASE_DIR / exe_name
    if local_path.exists():
        return str(local_path)

    import shutil
    which_result = shutil.which(exe_name)
    if which_result:
        return which_result

    return exe_name

# 默认配置
DEFAULT_CONFIG = {
    "uploader_list": [],
    "downloaded_videos": [],
    "settings": {
        "primary_tool": "bbdown",
        "fallback_tool": "yt-dlp",
        "resolution": "360p",
        "max_videos": 15,
        "cookie_file": "",
        "download_mode": "audio"  # audio / video
    }
}


class LogLevel(Enum):
    """日志级别"""
    INFO  = "INFO"
    WARN  = "WARN"
    ERROR = "ERROR"


class ConfigManager:
    """配置文件的加载、保存和管理"""

    def __init__(self, config_path=CONFIG_FILE):
        self.config_path = config_path
        self.data = self._load()

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 补全缺失的顶层字段
                    for key in DEFAULT_CONFIG:
                        if key not in data:
                            data[key] = DEFAULT_CONFIG[key]
                    # 补全缺失的 settings 子字段
                    for key in DEFAULT_CONFIG["settings"]:
                        if key not in data["settings"]:
                            data["settings"][key] = DEFAULT_CONFIG["settings"][key]
                    # 旧配置兼容：download_tool -> primary_tool
                    if "download_tool" in data["settings"] and "primary_tool" not in data["settings"]:
                        data["settings"]["primary_tool"] = data["settings"].pop("download_tool")
                    return data
            except (json.JSONDecodeError, IOError):
                return DEFAULT_CONFIG.copy()
        return DEFAULT_CONFIG.copy()

    def save(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_uploader(self, name, url):
        url = self._normalize_url(url)
        for up in self.data["uploader_list"]:
            if up["url"] == url:
                return False, "该UP主已存在"
        self.data["uploader_list"].append({"name": name, "url": url})
        self.save()
        return True, "添加成功"

    def remove_uploader(self, url):
        self.data["uploader_list"] = [
            up for up in self.data["uploader_list"] if up["url"] != url
        ]
        self.save()

    def add_downloaded(self, video_url):
        video_id = self._extract_video_id(video_url)
        if video_id and video_id not in self.data["downloaded_videos"]:
            self.data["downloaded_videos"].append(video_id)
            self.save()
            return True
        return False

    def is_downloaded(self, video_url):
        video_id = self._extract_video_id(video_url)
        return video_id in self.data["downloaded_videos"]

    def _normalize_url(self, url):
        match = re.search(r'space\.bilibili\.com/(\d+)', url)
        if match:
            uid = match.group(1)
            return f"https://space.bilibili.com/{uid}/video"
        if "bilibili.com" not in url:
            return url
        return url.rstrip('/') + "/video" if "/video" not in url else url

    def _extract_video_id(self, url):
        bv_match = re.search(r'(BV[a-zA-Z0-9]+)', url)
        if bv_match:
            return bv_match.group(1)
        av_match = re.search(r'av(\d+)', url, re.I)
        if av_match:
            return f"av{av_match.group(1)}"
        return url


class Logger:

    def __init__(self, log_callback=None, log_file=LOG_FILE):
        self._callback = log_callback or print
        self._log_file = log_file

    def _write(self, level: LogLevel, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {
            LogLevel.INFO:  "[I] ",
            LogLevel.WARN:  "[W] ",
            LogLevel.ERROR: "[E] ",
        }[level]
        formatted = f"[{timestamp}][{level.value}] {prefix}{message}"
        print(formatted)
        try:
            with open(self._log_file, 'a', encoding='utf-8') as f:
                f.write(formatted + "\n")
        except Exception:
            pass
        self._callback(f"{prefix}{message}")

    def info(self, msg):  self._write(LogLevel.INFO,  msg)
    def warn(self, msg):  self._write(LogLevel.WARN,  msg)
    def error(self, msg): self._write(LogLevel.ERROR, msg)

    def __call__(self, msg):
        self.info(msg)


class DownloadError(Enum):
    """下载错误分类"""
    NETWORK   = "network"    # 超时/连接失败 -> 重试
    COOKIE    = "cookie"     # 需要登录      -> 提示用户
    NOT_FOUND = "not_found"  # 视频不存在    -> 跳过
    UNKNOWN   = "unknown"    # 其他          -> 记录并跳过

def classify_error(stderr: str) -> DownloadError:
    """根据 stderr 文本判断错误类型"""
    text = (stderr or "").lower()
    if any(k in text for k in ["timeout", "connection", "network", "timed out", "连接超时"]):
        return DownloadError.NETWORK
    if any(k in text for k in ["login", "cookie", "403", "need login", "请登录", "-10403"]):
        return DownloadError.COOKIE
    if any(k in text for k in ["404", "not found", "does not exist", "视频不存在", "已失效"]):
        return DownloadError.NOT_FOUND
    return DownloadError.UNKNOWN


class DownloadStrategy(ABC):
    """下载策略基类，各具体下载工具需继承并实现 download 方法"""

    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.log = logger
        self.stop_flag = False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def download(self, video_url: str, mode: str) -> tuple[bool, DownloadError | None]:
        """
        执行下载。
        返回 (success: bool, error_type: DownloadError | None)
        """
        ...

    def _cookie_args(self, style: str = "yt-dlp") -> list:
        """返回 cookie 参数列表"""
        cookie_file = self.config.data["settings"].get("cookie_file", "")
        if not cookie_file or not Path(cookie_file).exists():
            return []
        if style == "yt-dlp":
            return ["--cookies", cookie_file]
        if style == "bbdown":
            return ["-c", cookie_file]
        if style == "you-get":
            return ["-c", cookie_file]
        return []

    def _run(self, cmd: list, timeout: int = 600) -> tuple[int, str]:
        """运行子进程，流式输出日志，返回 (returncode, combined_output)"""
        self.log.info(f"执行: {' '.join(str(c) for c in cmd)}")
        output_lines = []
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1
            )
            last_line = ""
            for line in iter(process.stdout.readline, ''):
                if self.stop_flag:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill()
                    return -1, "\n".join(output_lines)

                line = line.rstrip()
                if not line:
                    continue
                output_lines.append(line)
                lower = line.lower()
                # 进度相关行：只打印变化的行
                if '%' in line or 'downloading' in lower or '下载' in lower or 'extract' in lower:
                    if line != last_line:
                        self.log.info(f"   {line[:120]}")
                        last_line = line
                elif any(k in lower for k in ['error', 'failed', '错误', '失败', 'unable', 'exception']):
                    self.log.warn(f"   {line[:200]}")

            process.wait(timeout=timeout)
            return process.returncode, "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            self.log.error(f"执行超时 ({timeout}s)")
            try: process.kill()
            except Exception: pass
            return -2, "timeout"
        except FileNotFoundError:
            self.log.error(f"未找到命令: {cmd[0]}")
            return -3, "not_found"
        except Exception as e:
            self.log.error(f"执行异常: {e}")
            return -4, str(e)


class BBDownStrategy(DownloadStrategy):
    """BBDown 下载策略"""

    name = "BBDown"

    QUALITY_MAP = {
        "最低": "最低画质",
        "360p": "360P",
        "480p": "480P",
        "720p": "720P",
        "1080p": "1080P 高码率"
    }

    def download(self, video_url: str, mode: str) -> tuple[bool, DownloadError | None]:
        bbdown_path = find_tool("BBDown")
        bbdown_dir = DOWNLOAD_DIR / "bbdown"
        bbdown_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            bbdown_path,
            video_url,
            "--work-dir", str(bbdown_dir),
            "-F", "<bvid>",
            "--skip-subtitle",
            "--skip-cover",
            "--force-http"
        ]

        if mode == "audio":
            cmd.append("--audio-only")
        else:
            resolution = self.config.data["settings"]["resolution"]
            quality = self.QUALITY_MAP.get(resolution)
            if quality:
                cmd.extend(["-q", quality])

        cmd.extend(self._cookie_args("bbdown"))

        self.log.info(f"[BBDown] 开始下载 ({mode} 模式)...")
        rc, output = self._run(cmd)
        if rc == 0:
            self.log.info("[BBDown] 下载完成")
            return True, None
        err = classify_error(output)
        self.log.error(f"[BBDown] 失败 (rc={rc}, type={err.value})")
        return False, err


class YtDlpStrategy(DownloadStrategy):
    """yt-dlp 下载策略，支持音频/视频模式"""

    name = "yt-dlp"

    RESOLUTION_MAP = {
        "最低": "worst[ext=mp4]/worst",
        "360p": "worst[height<=360][ext=mp4]/worst[height<=480][ext=mp4]/worst",
        "480p": "worst[height<=480][ext=mp4]/worst[height<=720][ext=mp4]/worst",
        "720p": "best[height<=720][ext=mp4]/best[height<=1080][ext=mp4]",
        "1080p": "best[height<=1080][ext=mp4]/best"
    }

    def download(self, video_url: str, mode: str) -> tuple[bool, DownloadError | None]:
        if mode == "audio":
            return self._download_audio(video_url)
        else:
            return self._download_video(video_url)

    def _download_audio(self, video_url: str) -> tuple[bool, DownloadError | None]:
        """yt-dlp 直接提取音频，元数据来自 --print"""
        meta = self._fetch_metadata(video_url)

        output_template = str(DOWNLOAD_DIR / "音频" / "%(uploader)s" / "%(title).80s.%(ext)s")

        ytdlp = find_tool("yt-dlp")
        cmd = [
            ytdlp,
            "-x",
            "--audio-format", "aac",
            "--embed-thumbnail",
            "--add-metadata",
            "-o", output_template,
            "--no-playlist",
            "--no-mtime",
            "--encoding", "utf-8",
            "--no-check-certificates",
            "--extractor-retries", "3",
            "--retries", "3",
        ]
        cmd.extend(self._cookie_args("yt-dlp"))
        cmd.append(video_url)

        self.log.info(f"[yt-dlp] 开始提取音频...")
        rc, output = self._run(cmd)
        if rc == 0:
            self.log.info(f"[yt-dlp] 音频下载完成")
            if meta:
                self.log.info(f"   标题: {meta.get('title', '?')} | 作者: {meta.get('uploader', '?')}")
            return True, None
        err = classify_error(output)
        self.log.error(f"[yt-dlp] 音频失败 (rc={rc}, type={err.value})")
        return False, err

    def _download_video(self, video_url: str) -> tuple[bool, DownloadError | None]:
        resolution = self.config.data["settings"]["resolution"]
        fmt = self.RESOLUTION_MAP.get(resolution, "worst")
        output_template = str(DOWNLOAD_DIR / "%(uploader)s" / "%(title).80s.%(ext)s")

        ytdlp = find_tool("yt-dlp")
        cmd = [
            ytdlp,
            "-f", fmt,
            "-o", output_template,
            "--no-playlist",
            "--no-mtime",
            "--progress",
            "--newline",
            "--encoding", "utf-8",
            "--no-check-certificates",
            "--extractor-retries", "3",
            "--retries", "3",
        ]
        cmd.extend(self._cookie_args("yt-dlp"))
        cmd.append(video_url)

        self.log.info(f"[yt-dlp] 开始下载视频...")
        rc, output = self._run(cmd)
        if rc == 0:
            self.log.info("[yt-dlp] 视频下载完成")
            return True, None
        err = classify_error(output)
        self.log.error(f"[yt-dlp] 视频失败 (rc={rc}, type={err.value})")
        return False, err

    def _fetch_metadata(self, video_url: str) -> dict | None:
        """使用 yt-dlp --print 从 B站 API 获取真实元数据"""
        ytdlp = find_tool("yt-dlp")
        cmd = [
            ytdlp,
            "--no-download",
            "--print", "%(title)s\n%(uploader)s\n%(uploader_id)s",
            "--no-warnings",
        ]
        cmd.extend(self._cookie_args("yt-dlp"))
        cmd.append(video_url)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=30
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) >= 2:
                    return {
                        "title":       lines[0].strip(),
                        "uploader":    lines[1].strip(),
                        "uploader_id": lines[2].strip() if len(lines) > 2 else ""
                    }
        except Exception as e:
            self.log.warn(f"元数据获取失败: {e}")
        return None

    def download_video_then_extract(self, video_url: str) -> tuple[bool, DownloadError | None]:
        """下载视频临时文件，再用 ffmpeg 提取音频（供 fallback 使用）"""
        output_template = str(DOWNLOAD_DIR / "_temp" / "%(uploader)s" / "%(title).80s.%(ext)s")
        resolution = self.config.data["settings"]["resolution"]
        fmt = self.RESOLUTION_MAP.get(resolution, "best")

        ytdlp = find_tool("yt-dlp")
        cmd = [
            ytdlp,
            "-f", fmt,
            "-o", output_template,
            "--no-playlist",
            "--no-mtime",
            "--encoding", "utf-8",
            "--no-check-certificates",
            "--extractor-retries", "3",
            "--retries", "3",
            "--print", "after_move:filepath",
        ]
        cmd.extend(self._cookie_args("yt-dlp"))
        cmd.append(video_url)

        self.log.info("[yt-dlp] 下载视频（临时，后续提取音频）...")
        rc, output = self._run(cmd, timeout=600)
        if rc != 0:
            err = classify_error(output)
            self.log.error(f"[yt-dlp] 视频下载失败 (type={err.value})")
            return False, err

        # 从输出中找到下载文件的路径
        video_path = None
        for line in reversed(output.split('\n')):
            line = line.strip()
            if line and Path(line).exists():
                video_path = line
                break

        if not video_path:
            self.log.error("[yt-dlp] 无法定位下载的视频文件")
            return False, DownloadError.UNKNOWN

        return self._extract_and_cleanup(video_path, video_url)

    def _extract_and_cleanup(self, video_path: str, video_url: str) -> tuple[bool, DownloadError | None]:
        """使用 ffmpeg 提取音频并写入真实元数据"""
        video_path = Path(video_path)
        audio_dir = DOWNLOAD_DIR / "音频" / "提取"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_file = audio_dir / (video_path.stem + ".aac")

        ffmpeg = find_tool("ffmpeg")
        # 先尝试无损复制音频流
        cmd_copy = [
            ffmpeg, "-i", str(video_path),
            "-vn", "-acodec", "copy", "-y", str(audio_file)
        ]
        self.log.info("ffmpeg: 尝试复制音频流...")
        result = subprocess.run(cmd_copy, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not audio_file.exists() or audio_file.stat().st_size == 0:
            # 复制失败，转码为 AAC
            self.log.warn("复制失败，转码为 AAC...")
            cmd_enc = [
                ffmpeg, "-i", str(video_path),
                "-vn", "-acodec", "aac", "-b:a", "192k", "-y", str(audio_file)
            ]
            result = subprocess.run(cmd_enc, capture_output=True, text=True, timeout=600)

        if not audio_file.exists() or audio_file.stat().st_size == 0:
            self.log.error("ffmpeg 音频提取失败")
            return False, DownloadError.UNKNOWN

        # 获取真实元数据并写入
        meta = self._fetch_metadata(video_url)
        self._write_metadata(audio_file, meta)

        # 删除临时视频文件
        try:
            video_path.unlink()
            self.log.info("已删除临时视频文件")
        except Exception as e:
            self.log.warn(f"删除临时文件失败: {e}")

        self.log.info(f"音频提取完成: {audio_file.name}")
        return True, None

    def _write_metadata(self, audio_file: Path, meta: dict | None):
        """用真实元数据写入 AAC 文件的 ID3 标签"""
        if meta:
            title   = meta.get("title", audio_file.stem)
            artist  = meta.get("uploader", "未知")
        else:
            # 从文件名尝试解析 "艺术家 - 标题" 格式
            title = audio_file.stem
            artist = "未知"
            if " - " in title:
                parts = title.split(" - ", 1)
                artist, title = parts[0].strip(), parts[1].strip()

        ffmpeg = find_tool("ffmpeg")
        tmp = audio_file.with_suffix(".tmp.aac")
        cmd = [
            ffmpeg,
            "-i", str(audio_file),
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}",
            "-metadata", "album=B站音乐",
            "-codec", "copy",
            "-y", str(tmp)
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
            if tmp.exists():
                tmp.replace(audio_file)
                self.log.info(f"已写入元数据: 标题={title} | 艺术家={artist}")
        except Exception as e:
            self.log.warn(f"元数据写入失败: {e}")


class YouGetStrategy(DownloadStrategy):
    """you-get 下载策略"""

    name = "you-get"

    def download(self, video_url: str, mode: str) -> tuple[bool, DownloadError | None]:
        if mode == "audio":
            return self._download_audio_via_video(video_url)
        return self._download_video(video_url)

    def _download_video(self, video_url: str) -> tuple[bool, DownloadError | None]:
        youget = find_tool("you-get")
        cmd = [
            youget,
            "--no-caption",
            "-o", str(DOWNLOAD_DIR),
            video_url
        ]
        cmd.extend(self._cookie_args("you-get"))
        self.log.info("[you-get] 开始下载视频...")
        rc, output = self._run(cmd)
        if rc == 0:
            self.log.info("[you-get] 下载完成")
            return True, None
        err = classify_error(output)
        self.log.error(f"[you-get] 失败 (type={err.value})")
        return False, err

    def _download_audio_via_video(self, video_url: str) -> tuple[bool, DownloadError | None]:
        """you-get 下载视频后用 ffmpeg 提取音频"""
        temp_dir = DOWNLOAD_DIR / "_temp_youget"
        temp_dir.mkdir(parents=True, exist_ok=True)

        youget = find_tool("you-get")
        cmd = [
            youget,
            "--no-caption",
            "-o", str(temp_dir),
            video_url
        ]
        cmd.extend(self._cookie_args("you-get"))
        self.log.info("[you-get] 下载视频（临时，后续提取音频）...")

        before = set(temp_dir.iterdir()) if temp_dir.exists() else set()
        rc, output = self._run(cmd, timeout=600)
        if rc != 0:
            err = classify_error(output)
            self.log.error(f"[you-get] 失败 (type={err.value})")
            return False, err

        after = set(temp_dir.iterdir())
        new_files = after - before
        video_path = None
        for f in sorted(new_files, key=lambda p: p.stat().st_size, reverse=True):
            if f.suffix.lower() in ['.mp4', '.flv', '.mkv', '.webm']:
                video_path = f
                break

        if not video_path:
            self.log.error("[you-get] 无法找到下载的视频文件")
            return False, DownloadError.UNKNOWN

        # 借用 YtDlpStrategy 的提取逻辑处理音视频分离和元数据写入
        ytdlp = YtDlpStrategy(self.config, self.log)
        ytdlp.stop_flag = self.stop_flag
        return ytdlp._extract_and_cleanup(str(video_path), video_url)


# 策略工厂：根据工具名创建对应的下载策略实例
STRATEGY_MAP = {
    "bbdown":  BBDownStrategy,
    "yt-dlp":  YtDlpStrategy,
    "you-get": YouGetStrategy,
}

def make_strategy(tool_name: str, config: ConfigManager, logger: Logger) -> DownloadStrategy | None:
    cls = STRATEGY_MAP.get(tool_name.lower())
    if cls is None:
        logger.warn(f"未知下载工具: {tool_name}")
        return None
    return cls(config, logger)


class AudioDownloadEngine:
    """
    策略模式下载管理器：
    - 首选工具失败 -> 根据错误类型决定是否切换备用工具
    - 最多两级（首选 + 备用）
    - 网络错误最多重试 MAX_RETRIES 次
    """
    MAX_RETRIES = 2

    def __init__(self, config: ConfigManager, logger: Logger):
        self.config  = config
        self.log     = logger
        self.stop_flag = False

    def _sync_stop(self, strategy: DownloadStrategy):
        strategy.stop_flag = self.stop_flag

    def run(self, video_url: str, mode: str) -> bool:
        """执行两级下载策略，返回是否最终成功"""
        primary_name  = self.config.data["settings"].get("primary_tool",  "bbdown")
        fallback_name = self.config.data["settings"].get("fallback_tool", "yt-dlp")

        primary  = make_strategy(primary_name,  self.config, self.log)
        fallback = make_strategy(fallback_name, self.config, self.log)

        # 首选工具（含网络重试）
        if primary:
            success, err = self._try_with_retry(primary, video_url, mode)
            if success:
                self.log.info(f"下载成功，使用工具: [{primary.name}]")
                return True
            if err == DownloadError.COOKIE:
                self.log.error("Cookie 错误：请检查 Cookie 文件是否有效或已过期，更新后重试。")
                return False
            if err == DownloadError.NOT_FOUND:
                self.log.warn("视频不存在或已失效，跳过。")
                return False
            # 其他错误 -> 尝试备用工具
            self.log.warn(f"首选工具 [{primary.name}] 失败，切换备用工具 [{fallback_name}]...")

        # 备用工具（含网络重试）
        if fallback:
            success, err = self._try_with_retry(fallback, video_url, mode)
            if success:
                self.log.info(f"下载成功，使用工具: [{fallback.name}]（备用）")
                return True
            if err == DownloadError.COOKIE:
                self.log.error("Cookie 错误：备用工具也需要有效的 Cookie，请检查后重试。")
            elif err == DownloadError.NOT_FOUND:
                self.log.warn("视频不存在或已失效，跳过。")
            else:
                self.log.error(f"备用工具 [{fallback.name}] 也失败，放弃本视频。")
            return False

        self.log.error("无可用下载工具，请检查配置。")
        return False

    def _try_with_retry(
        self, strategy: DownloadStrategy, video_url: str, mode: str
    ) -> tuple[bool, DownloadError | None]:
        """对网络错误进行重试，最多重试 MAX_RETRIES 次（即总共尝试 MAX_RETRIES+1 次）"""
        for attempt in range(self.MAX_RETRIES + 1):
            self._sync_stop(strategy)
            if self.stop_flag:
                return False, DownloadError.UNKNOWN
            if attempt > 0:
                self.log.warn(f"网络重试 {attempt}/{self.MAX_RETRIES}...")

            # yt-dlp 音频模式：先试直接提取，失败则尝试先下载视频再提取
            if isinstance(strategy, YtDlpStrategy) and mode == "audio":
                success, err = strategy.download(video_url, mode)
                if not success and err not in (DownloadError.COOKIE, DownloadError.NOT_FOUND):
                    self.log.warn("[yt-dlp] 直接音频提取失败，尝试先下载视频再提取...")
                    success, err = strategy.download_video_then_extract(video_url)
            else:
                success, err = strategy.download(video_url, mode)

            if success:
                return True, None
            if err != DownloadError.NETWORK:
                return False, err
            # 网络错误则继续重试

        return False, DownloadError.NETWORK


class VideoDownloader:
    """
    对外暴露 download_video / download_all / stop / get_video_list 接口，
    内部委托给 AudioDownloadEngine 和各 DownloadStrategy。
    """

    RESOLUTION_MAP = {
        "最低": "worst[ext=mp4]/worst",
        "360p": "worst[height<=360][ext=mp4]/worst[height<=480][ext=mp4]/worst",
        "480p": "worst[height<=480][ext=mp4]/worst[height<=720][ext=mp4]/worst",
        "720p": "best[height<=720][ext=mp4]/best[height<=1080][ext=mp4]",
        "1080p": "best[height<=1080][ext=mp4]/best"
    }

    def __init__(self, config: ConfigManager, log_callback=None):
        self.config = config
        self.log = Logger(log_callback)
        self.stop_flag = False
        self._engine = AudioDownloadEngine(config, self.log)

    def get_video_list(self, uploader_url: str) -> list:
        """通过 yt-dlp --flat-playlist 获取指定UP主的视频列表"""
        max_videos = self.config.data["settings"]["max_videos"]
        ytdlp = find_tool("yt-dlp")
        cmd = [
            ytdlp,
            "--flat-playlist",
            "--print", "%(webpage_url)s",
            "--playlist-end", str(max_videos),
            "--no-warnings",
            uploader_url
        ]
        cookie_file = self.config.data["settings"].get("cookie_file", "")
        if cookie_file and Path(cookie_file).exists():
            cmd.extend(["--cookies", cookie_file])

        try:
            self.log.info(f"获取视频列表: {uploader_url}")
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=180, encoding='utf-8', errors='replace'
            )
            if result.returncode != 0:
                self.log.error(f"获取失败: {result.stderr[:200]}")
                return []
            urls = [ln.strip() for ln in result.stdout.strip().split('\n') if ln.strip()]
            self.log.info(f"获取到 {len(urls)} 个视频")
            return urls
        except subprocess.TimeoutExpired:
            self.log.error("获取视频列表超时")
            return []
        except FileNotFoundError:
            self.log.error("未找到 yt-dlp，请先安装: pip install yt-dlp")
            return []
        except Exception as e:
            self.log.error(f"获取出错: {e}")
            return []

    def filter_new_videos(self, video_urls: list) -> list:
        """过滤已下载的视频，返回新视频列表"""
        new_videos = [u for u in video_urls if not self.config.is_downloaded(u)]
        filtered = len(video_urls) - len(new_videos)
        if filtered > 0:
            self.log.info(f"已过滤 {filtered} 个已下载视频")
        self.log.info(f"待下载: {len(new_videos)} 个新视频")
        return new_videos

    def download_video(self, video_url: str) -> bool:
        """下载单个视频，使用配置中的下载模式"""
        mode = self.config.data["settings"].get("download_mode", "audio")
        self._engine.stop_flag = self.stop_flag

        success = self._engine.run(video_url, mode)
        if success:
            self.config.add_downloaded(video_url)
        return success

    def download_all(self) -> tuple[int, int]:
        """批量下载所有UP主的新视频，返回 (成功数, 失败数)"""
        self.stop_flag = False
        uploaders = self.config.data["uploader_list"]
        if not uploaders:
            self.log.warn("请先添加UP主")
            return 0, 0

        mode = self.config.data["settings"].get("download_mode", "audio")
        mode_str = "音频" if mode == "audio" else "视频"
        primary  = self.config.data["settings"].get("primary_tool",  "bbdown")
        fallback = self.config.data["settings"].get("fallback_tool", "yt-dlp")

        self.log(f"\n{'='*50}")
        self.log(f"开始下载任务 - {mode_str}模式，共 {len(uploaders)} 个UP主")
        self.log(f"下载目录: {DOWNLOAD_DIR}")
        self.log(f"工具策略: 首选=[{primary}] 备用=[{fallback}]")
        self.log(f"{'='*50}\n")

        total_downloaded = 0
        total_failed = 0
        failed_videos = []

        for idx, uploader in enumerate(uploaders, 1):
            if self.stop_flag:
                self.log.warn("用户停止下载")
                break

            self.log(f"\n[{idx}/{len(uploaders)}] {uploader['name']}")
            self.log("-" * 40)

            video_urls = self.get_video_list(uploader["url"])
            new_videos = self.filter_new_videos(video_urls)

            for video_url in new_videos:
                if self.stop_flag:
                    break
                success = self.download_video(video_url)
                if success:
                    total_downloaded += 1
                else:
                    total_failed += 1
                    failed_videos.append(video_url)

        self.log(f"\n{'='*50}")
        self.log(f"下载任务完成!")
        self.log(f"   成功: {total_downloaded} 个")
        self.log(f"   失败: {total_failed} 个")
        if failed_videos:
            self.log("以下链接失败:")
            for url in failed_videos[:5]:
                self.log(f"   - {url}")
            if len(failed_videos) > 5:
                self.log(f"   ... 共 {len(failed_videos)} 个")
        self.log(f"{'='*50}\n")
        return total_downloaded, total_failed

    def stop(self):
        self.stop_flag = True
        self._engine.stop_flag = True


class Application:
    """GUI 主界面"""

    def __init__(self):
        self.config = ConfigManager()
        self.downloader = VideoDownloader(self.config, self._log)
        self.download_thread = None
        self.is_downloading = False

        self._create_ui()
        self._load_uploader_list()

    def _create_ui(self):
        self.root = tk.Tk()
        self.root.title("B站关注UP主批量下载器")
        self.root.geometry("850x700")
        self.root.minsize(750, 500)

        style = ttk.Style()
        if sys.platform == 'linux':
            try: style.theme_use('clam')
            except Exception: pass

        main = ttk.Frame(self.root, padding="10")
        main.pack(fill=tk.BOTH, expand=True)

        # 左侧面板
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # UP主列表
        list_frame = ttk.LabelFrame(left, text=" UP主列表 ", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "url")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=6)
        self.tree.heading("name", text="UP主名称")
        self.tree.heading("url", text="主页链接")
        self.tree.column("name", width=100, minwidth=80)
        self.tree.column("url", width=350, minwidth=200)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 添加UP主
        add_frame = ttk.Frame(left)
        add_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(add_frame, text="名称:").pack(side=tk.LEFT)
        self.name_entry = ttk.Entry(add_frame, width=12)
        self.name_entry.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(add_frame, text="链接:").pack(side=tk.LEFT)
        self.url_entry = ttk.Entry(add_frame, width=30)
        self.url_entry.pack(side=tk.LEFT, padx=(2, 8), fill=tk.X, expand=True)
        btn_frame = ttk.Frame(add_frame)
        btn_frame.pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="添加", width=8, command=self._add_uploader).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除", width=8, command=self._remove_uploader).pack(side=tk.LEFT, padx=2)

        # 运行日志
        log_frame = ttk.LabelFrame(left, text=" 运行日志 ", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=12, state=tk.DISABLED,
            font=('Consolas', 9) if sys.platform == 'win32' else ('Monospace', 9)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 右侧面板
        right = ttk.Frame(main, width=210)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right.pack_propagate(False)

        # 设置区域
        settings = ttk.LabelFrame(right, text=" 设置 ", padding="10")
        settings.pack(fill=tk.X)

        # 下载模式
        ttk.Label(settings, text="下载模式:").pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value=self.config.data["settings"].get("download_mode", "audio"))
        mode_frame = ttk.Frame(settings)
        mode_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Radiobutton(mode_frame, text="音频", variable=self.mode_var, value="audio").pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="视频", variable=self.mode_var, value="video").pack(side=tk.LEFT, padx=(10, 0))

        # 首选工具
        ttk.Label(settings, text="首选工具:").pack(anchor=tk.W)
        self.tool_var = tk.StringVar(value=self.config.data["settings"].get("primary_tool", "bbdown"))
        tool_frame = ttk.Frame(settings)
        tool_frame.pack(fill=tk.X, pady=(0, 8))
        for t in ["bbdown", "yt-dlp", "you-get"]:
            ttk.Radiobutton(tool_frame, text=t, variable=self.tool_var, value=t).pack(side=tk.LEFT, padx=(0, 4))

        # 备用工具
        ttk.Label(settings, text="备用工具:").pack(anchor=tk.W)
        self.fallback_var = tk.StringVar(value=self.config.data["settings"].get("fallback_tool", "yt-dlp"))
        fb_frame = ttk.Frame(settings)
        fb_frame.pack(fill=tk.X, pady=(0, 8))
        for t in ["yt-dlp", "you-get", "bbdown"]:
            ttk.Radiobutton(fb_frame, text=t, variable=self.fallback_var, value=t).pack(side=tk.LEFT, padx=(0, 4))

        # 分辨率
        ttk.Label(settings, text="视频分辨率:").pack(anchor=tk.W)
        self.res_var = tk.StringVar(value=self.config.data["settings"]["resolution"])
        ttk.Combobox(
            settings, textvariable=self.res_var,
            values=["最低", "360p", "480p", "720p", "1080p"],
            state="readonly", width=18
        ).pack(fill=tk.X, pady=(0, 2))
        ttk.Label(settings, text="(视频模式有效)", font=('', 7), foreground='gray').pack(anchor=tk.W, pady=(0, 5))

        # 获取数量
        ttk.Label(settings, text="每UP主获取数量:").pack(anchor=tk.W)
        self.max_var = tk.StringVar(value=str(self.config.data["settings"]["max_videos"]))
        ttk.Spinbox(settings, from_=5, to=50, textvariable=self.max_var, width=18).pack(fill=tk.X, pady=(0, 8))

        # Cookie 文件
        ttk.Label(settings, text="Cookie文件 (可选):").pack(anchor=tk.W)
        cookie_frame = ttk.Frame(settings)
        cookie_frame.pack(fill=tk.X, pady=(0, 8))
        self.cookie_var = tk.StringVar(value=self.config.data["settings"].get("cookie_file", ""))
        ttk.Entry(cookie_frame, textvariable=self.cookie_var, width=14).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(cookie_frame, text="浏览", width=3, command=self._browse_cookie).pack(side=tk.RIGHT)

        ttk.Button(settings, text="保存设置", command=self._save_settings).pack(fill=tk.X, pady=(5, 0))

        # 操作按钮
        actions = ttk.LabelFrame(right, text=" 操作 ", padding="10")
        actions.pack(fill=tk.X, pady=(10, 0))

        self.start_btn = ttk.Button(actions, text="开始下载", command=self._start_download)
        self.start_btn.pack(fill=tk.X, pady=2)
        self.stop_btn = ttk.Button(actions, text="停止下载", command=self._stop_download, state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=2)
        ttk.Separator(actions, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        ttk.Button(actions, text="直接下载链接", command=self._download_url_dialog).pack(fill=tk.X, pady=2)
        ttk.Separator(actions, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        ttk.Button(actions, text="打开下载目录", command=self._open_folder).pack(fill=tk.X, pady=2)
        ttk.Button(actions, text="清空下载记录", command=self._clear_history).pack(fill=tk.X, pady=2)

        # 状态栏
        status_frame = ttk.Frame(right)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var, foreground="gray").pack()
        downloaded_count = len(self.config.data["downloaded_videos"])
        self.stats_label = ttk.Label(
            status_frame, text=f"已下载: {downloaded_count} 个",
            foreground="gray", font=('', 8)
        )
        self.stats_label.pack()

    def _log(self, message):
        """日志输出：同时打印到控制台和GUI文本框"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")
        def update():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, update)

    def _update_stats(self):
        count = len(self.config.data["downloaded_videos"])
        self.stats_label.config(text=f"已下载: {count} 个")

    def _load_uploader_list(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for up in self.config.data["uploader_list"]:
            self.tree.insert("", tk.END, values=(up["name"], up["url"]))

    def _add_uploader(self):
        name = self.name_entry.get().strip()
        url  = self.url_entry.get().strip()
        if not name or not url:
            messagebox.showwarning("提示", "请输入名称和链接")
            return
        if "bilibili.com" not in url:
            messagebox.showwarning("提示", "请输入有效的B站链接")
            return
        success, msg = self.config.add_uploader(name, url)
        if success:
            self._load_uploader_list()
            self.name_entry.delete(0, tk.END)
            self.url_entry.delete(0, tk.END)
            self._log(f"添加UP主: {name}")
        else:
            messagebox.showinfo("提示", msg)

    def _remove_uploader(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要删除的UP主")
            return
        if messagebox.askyesno("确认删除", "确定要删除选中的UP主吗?"):
            for item in selected:
                values = self.tree.item(item, "values")
                self.config.remove_uploader(values[1])
                self._log(f"删除UP主: {values[0]}")
            self._load_uploader_list()

    def _browse_cookie(self):
        filename = filedialog.askopenfilename(
            title="选择Cookie文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if filename:
            self.cookie_var.set(filename)

    def _save_settings(self):
        try:
            max_videos = int(self.max_var.get())
            if max_videos < 1: raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "获取数量必须是正整数")
            return

        self.config.data["settings"]["download_mode"] = self.mode_var.get()
        self.config.data["settings"]["primary_tool"]  = self.tool_var.get()
        self.config.data["settings"]["fallback_tool"] = self.fallback_var.get()
        self.config.data["settings"]["resolution"]    = self.res_var.get()
        self.config.data["settings"]["max_videos"]    = max_videos
        self.config.data["settings"]["cookie_file"]   = self.cookie_var.get()
        self.config.save()
        self._log("设置已保存")

    def _start_download(self):
        if not self.config.data["uploader_list"]:
            messagebox.showwarning("提示", "请先添加UP主")
            return

        self._save_settings()
        self.is_downloading = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("下载中...")

        def task():
            try:
                self.downloader.download_all()
            finally:
                self.root.after(0, self._on_download_complete)

        self.download_thread = threading.Thread(target=task, daemon=True)
        self.download_thread.start()

    def _stop_download(self):
        self.downloader.stop()
        self._log("正在停止...")

    def _on_download_complete(self):
        self.is_downloading = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_var.set("就绪")
        self._update_stats()

    def _open_folder(self):
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(DOWNLOAD_DIR)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(DOWNLOAD_DIR)])
        else:
            subprocess.run(["xdg-open", str(DOWNLOAD_DIR)])

    def _clear_history(self):
        count = len(self.config.data["downloaded_videos"])
        if count == 0:
            messagebox.showinfo("提示", "下载记录已经是空的")
            return
        if messagebox.askyesno(
            "确认清空",
            f"确定要清空 {count} 条下载记录吗?\n\n这将导致已下载的视频在下次运行时被重新识别为新视频。"
        ):
            self.config.data["downloaded_videos"] = []
            self.config.save()
            self._log(f"已清空 {count} 条下载记录")
            self._update_stats()

    def _download_url_dialog(self):
        """弹出对话框让用户输入B站链接并直接下载"""
        dialog = tk.Toplevel(self.root)
        dialog.title("直接下载视频")
        dialog.geometry("500x220")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 500) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 220) // 2
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text="输入哔哩哔哩视频链接（支持 BV号、av号、视频页面URL）：",
                  wraplength=460).pack(padx=15, pady=(15, 5), anchor=tk.W)

        url_var = tk.StringVar()
        url_entry = ttk.Entry(dialog, textvariable=url_var, width=60)
        url_entry.pack(padx=15, fill=tk.X)
        url_entry.focus_set()

        # 下载模式（继承当前设置，允许临时覆盖）
        mode_frame = ttk.LabelFrame(dialog, text=" 本次下载模式 ", padding="5")
        mode_frame.pack(padx=15, pady=8, fill=tk.X)
        mode_var = tk.StringVar(value=self.config.data["settings"].get("download_mode", "audio"))
        ttk.Radiobutton(mode_frame, text="音频 (AAC)", variable=mode_var, value="audio").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(mode_frame, text="视频", variable=mode_var, value="video").pack(side=tk.LEFT, padx=10)

        status_var = tk.StringVar(value="")
        ttk.Label(dialog, textvariable=status_var, foreground="gray").pack()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=(5, 10))

        def do_download():
            url = url_var.get().strip()
            if not url:
                messagebox.showwarning("提示", "请输入视频链接", parent=dialog)
                return
            if "bilibili.com" not in url and not re.match(r'^(BV|bv|av|AV)', url):
                messagebox.showwarning("提示", "请输入有效的B站链接或BV号/av号", parent=dialog)
                return

            # 将 BV/av 号补全为完整 URL
            full_url = url
            if re.match(r'^(BV|bv)[a-zA-Z0-9]+$', url):
                full_url = f"https://www.bilibili.com/video/{url}"
            elif re.match(r'^(av|AV)\d+$', url):
                full_url = f"https://www.bilibili.com/video/{url}"

            download_btn.config(state=tk.DISABLED)
            cancel_btn.config(state=tk.DISABLED)
            status_var.set("下载中，请稍候...")
            dialog.update()

            # 临时保存当前模式，使用用户选择的模式
            orig_mode = self.config.data["settings"].get("download_mode", "audio")
            self.config.data["settings"]["download_mode"] = mode_var.get()

            self._log(f"\n直接下载: {full_url}")
            self._log(f"   模式: {'音频' if mode_var.get() == 'audio' else '视频'} | 工具: {self.config.data['settings'].get('primary_tool','bbdown')} -> {self.config.data['settings'].get('fallback_tool','yt-dlp')}")

            def task():
                try:
                    success = self.downloader.download_video(full_url)
                    def done():
                        # 恢复原始模式
                        self.config.data["settings"]["download_mode"] = orig_mode
                        if success:
                            status_var.set("下载完成!")
                            self._log(f"直接下载成功: {full_url}")
                            self._update_stats()
                        else:
                            status_var.set("下载失败，请查看日志")
                            self._log(f"直接下载失败: {full_url}")
                        download_btn.config(state=tk.NORMAL)
                        cancel_btn.config(state=tk.NORMAL)
                    dialog.after(0, done)
                except Exception as e:
                    def err_done():
                        self.config.data["settings"]["download_mode"] = orig_mode
                        status_var.set(f"错误: {e}")
                        download_btn.config(state=tk.NORMAL)
                        cancel_btn.config(state=tk.NORMAL)
                    dialog.after(0, err_done)

            threading.Thread(target=task, daemon=True).start()

        def on_enter(event):
            do_download()

        url_entry.bind("<Return>", on_enter)

        download_btn = ttk.Button(btn_frame, text="开始下载", command=do_download, width=14)
        download_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = ttk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10)
        cancel_btn.pack(side=tk.LEFT, padx=8)

    def run(self):
        self.root.mainloop()


def check_dependencies():
    """检查 yt-dlp、you-get、ffmpeg、BBDown 是否可用"""
    print("=" * 40)
    print("检查依赖工具...")
    print("=" * 40)

    ytdlp_ok = ffmpeg_ok = bbdown_ok = youget_ok = False

    ytdlp_path = find_tool("yt-dlp")
    try:
        r = subprocess.run([ytdlp_path, "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print(f"[OK] yt-dlp {r.stdout.strip()}")
            ytdlp_ok = True
    except Exception:
        print("[--] yt-dlp 未安装")

    youget_path = find_tool("you-get")
    try:
        r = subprocess.run([youget_path, "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print("[OK] you-get 已安装")
            youget_ok = True
    except Exception:
        print("[--] you-get 未安装")

    ffmpeg_path = find_tool("ffmpeg")
    try:
        r = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print("[OK] ffmpeg 已安装")
            ffmpeg_ok = True
    except Exception:
        print("[--] ffmpeg 未安装  (https://ffmpeg.org/)")

    bbdown_path = find_tool("BBDown")
    try:
        r = subprocess.run([bbdown_path, "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print(f"[OK] BBDown {r.stdout.strip()}")
            bbdown_ok = True
    except Exception:
        print("[--] BBDown 未找到，请将 BBDown.exe 放在程序目录下")

    print("=" * 40)

    if not ytdlp_ok:
        print("\n警告: yt-dlp 未安装，无法获取视频列表!")
        return False
    if not ffmpeg_ok:
        print("\n警告: ffmpeg 未安装，视频->音频提取功能将不可用!")
    print()
    return True


def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not check_dependencies():
        input("\n按回车键退出...")
        return
    app = Application()
    app.run()


if __name__ == "__main__":
    main()