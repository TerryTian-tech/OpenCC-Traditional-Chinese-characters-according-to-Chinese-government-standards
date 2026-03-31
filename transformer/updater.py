import ssl
import urllib.request
import urllib.error
import json
import re
import certifi

from PySide6.QtCore import QThread, Signal

from constants import VERSION


class UpdateChecker(QThread):
    update_checked = Signal(bool, str, str)  # (有新版本?, 新版本号, 下载页面URL)

    def run(self):
        try:
            url = "https://api.github.com/repos/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/releases/latest"
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, context=ssl_context, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                latest_tag = data.get('tag_name', '')

                # 使用正则提取版本号（从 "Transformer(1.2.4)" 中提取 "1.2.4"）
                match = re.search(r'(\d+\.\d+\.\d+)', latest_tag)
                if match:
                    latest_version = match.group(1)
                else:
                    # 如果标签格式不符合预期，仍使用原字符串，但后续比较会失败
                    latest_version = latest_tag

                download_url = 'https://gitee.com/terrytian-tech/tonggui-traditional-chinese/releases/latest'

                # 当前版本（已在程序开头定义）
                current_parts = [int(x) for x in VERSION.split('.')]
                try:
                    latest_parts = [int(x) for x in latest_version.split('.')]
                except ValueError:
                    # 提取到的版本号格式不正确，认为检查失败
                    self.update_checked.emit(False, '', f"无法解析最新版本号：{latest_version}")
                    return

                # 版本比较
                has_new = latest_parts > current_parts
                self.update_checked.emit(has_new, latest_version, download_url)

        except Exception as e:
            self.update_checked.emit(False, '', f"检查失败：{str(e)}")
