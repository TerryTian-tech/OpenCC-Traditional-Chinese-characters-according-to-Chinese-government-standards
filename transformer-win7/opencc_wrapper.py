import os
import subprocess
import threading
import atexit
import sys

if sys.platform == 'win32':
    _SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    _SUBPROCESS_FLAGS = 0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPENCC_EXE = os.path.join(BASE_DIR, 'opencc', 'bin', 'opencc.exe')
CONFIG_DIR = os.path.join(BASE_DIR, 'opencc', 'share', 'opencc')

# 使用 Supplementary Private Use Area-A (U+F0000–U+FFFFD) 字符作为占位符。
# 这些字符在标准文本中出现的概率几乎为零，从根本上避免与正常文本冲突。
_LF_PLACEHOLDER = '\U000F0000'
_CR_PLACEHOLDER = '\U000F0001'
_SENTINEL = '\U000F0002'

# IPC 读取超时（秒），防止 opencc 子进程死锁时无限阻塞
_READ_TIMEOUT = 30 


class ExternalOpenCC:
    """
    通过调用外部 opencc.exe 进程实现 OpenCC 转换功能的包装类。
    兼容 opencc.Python 包的 OpenCC 接口，用于替代无法通过 pip 安装的 Python opencc 包。
    兼容 OpenCC 1.3.1：
    1.3.1 将换行输出从行末（after each line）改为行间（between lines），
       即 fputs("\\n") 在 fputs(output) 之前，导致 readline() 在首次
       和末次调用时永远等不到换行符而卡死。
    本实现使用哨兵协议：每次写入 文本 + 哨兵 + 空行触发器，
    通过空行触发器使 OpenCC 输出哨兵后的换行，确保 readline() 可靠返回。
    """

    def __init__(self, config):
        config_path = os.path.join(CONFIG_DIR, config + '.json')
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"OpenCC config not found: {config_path}")
        if not os.path.exists(OPENCC_EXE):
            raise FileNotFoundError(f"OpenCC executable not found: {OPENCC_EXE}")

        self.config = config
        self._config_path = config_path
        self._lock = threading.Lock()
        self._proc = None
        self._is_first = True
        self._start_process()
        atexit.register(self.close)

    def _start_process(self):
        """启动或重启 opencc 子进程。"""
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except Exception:
                pass

        self._proc = subprocess.Popen(
            [OPENCC_EXE, '-c', self._config_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            bufsize=1,
            cwd=os.path.dirname(OPENCC_EXE),
            creationflags=_SUBPROCESS_FLAGS
        )
        self._is_first = True

    def _readline_with_timeout(self, timeout=_READ_TIMEOUT):
        """
        从 stdout 读取一行，带超时保护。
        如果超时，强制终止子进程并抛出 TimeoutError，避免无限阻塞。
        """
        result_container = [None]

        def _reader():
            try:
                result_container[0] = self._proc.stdout.readline()
            except Exception:
                # 进程可能已关闭，readline 会抛出异常，忽略
                pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        reader_thread.join(timeout)

        if reader_thread.is_alive():
            # 超时：强制 kill 子进程，使 readline() 因管道关闭而返回
            try:
                self._proc.kill()
            except Exception:
                pass
            reader_thread.join(timeout=2)  # 等待 readline 在管道关闭后返回
            raise TimeoutError(
                f"OpenCC process did not respond within {timeout}s; killed and will restart"
            )

        return result_container[0]

    def convert(self, text):
        """转换文本，保持与 opencc.OpenCC.convert 的接口一致。"""
        if text is None:
            return None
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return text

        # 转义换行符，将多行文本合并为单行传递给 opencc
        safe_text = text.replace('\r', _CR_PLACEHOLDER).replace('\n', _LF_PLACEHOLDER)

        with self._lock:
            for attempt in range(2):
                if self._proc is None or self._proc.poll() is not None:
                    self._start_process()

                # 哨兵协议写入格式：实际文本 + \n + 哨兵 + \n + 空行触发器(\n)
                # OpenCC 1.3.1 对这 3 行的输出（isFirstLine 在进程生命周期内只首次为 true）：
                #   首次调用：fputs(转换结果) fputs("\n") fputs(哨兵) fputs("\n") fputs("")
                #   → stdout: "转换结果\n哨兵\n"
                #   后续调用：fputs("\n") fputs(转换结果) fputs("\n") fputs(哨兵) fputs("\n") fputs("")
                #   → stdout: "\n转换结果\n哨兵\n"
                try:
                    self._proc.stdin.write(safe_text + '\n' + _SENTINEL + '\n\n')
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    if attempt == 0:
                        self._start_process()
                        continue
                    raise RuntimeError("OpenCC process write failed")

                # 非首次调用需跳过行间换行前缀
                if not self._is_first:
                    try:
                        skip_line = self._readline_with_timeout()
                    except TimeoutError:
                        if attempt == 0:
                            self._start_process()
                            continue
                        raise
                    if skip_line is None:
                        if attempt == 0:
                            self._start_process()
                            continue
                        raise RuntimeError("OpenCC process failed: skip-line read returned None")
                self._is_first = False

                # 读取转换结果
                try:
                    result = self._readline_with_timeout()
                except TimeoutError:
                    if attempt == 0:
                        self._start_process()
                        continue
                    raise RuntimeError("OpenCC process failed: read timeout")

                if result is None or not result.endswith('\n'):
                    if attempt == 0:
                        self._start_process()
                        continue
                    raise RuntimeError("OpenCC process failed: no newline in output")

                result = result[:-1]

                # 读取并验证哨兵
                try:
                    sentinel_line = self._readline_with_timeout()
                except TimeoutError:
                    if attempt == 0:
                        self._start_process()
                        continue
                    raise RuntimeError("OpenCC sentinel read timeout")

                if sentinel_line is None or not sentinel_line.endswith('\n') or sentinel_line[:-1] != _SENTINEL:
                    if attempt == 0:
                        self._start_process()
                        continue
                    raise RuntimeError("OpenCC sentinel mismatch, output protocol desync")

                # 还原转义的换行符
                result = result.replace(_LF_PLACEHOLDER, '\n').replace(_CR_PLACEHOLDER, '\r')
                return result

        raise RuntimeError("OpenCC convert failed after retries")

    def close(self):
        """关闭 opencc 子进程。"""
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None

    def __del__(self):
        self.close()


# 别名，方便直接替换导入
OpenCC = ExternalOpenCC
