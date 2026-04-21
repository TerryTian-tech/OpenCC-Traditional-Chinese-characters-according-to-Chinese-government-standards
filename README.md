# OpenCC-Traditional Chinese to Traditional Chinese (The Chinese Government Standard)
OpenCC开放中文转换 - 将混杂不同标准的繁体字形转换为《通用规范汉字表》（2013，内地现行法定标准）的规范繁体字形

[展示页面](https://terrytian-tech.github.io/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/)

## 仓库介绍
2013年国务院颁布了《通用规范汉字表》作为内地实施《中华人民共和国国家通用语言文字法》的配套规范，该表在确定内地简体字规范字形的同时，在“附件1”中收录了与之对应的繁体字形。虽然内地的输入法软件、繁简转换插件并不完全遵照该表的繁体字形，但是该表的字形被视作内地繁体出版的标准进行适用。2021年又有《古籍印刷通用字规范字形表》（GB/Z 40637—2021）颁布，但是该表存在两个重大缺陷：一是没有确立繁体正体字形和异体字形的标准，正体、异体不作区分全部收录；二是《通用规范汉字表》的部分字形，《古籍印刷通用字规范字形表》不收，而《古籍印刷通用字规范字形表》在法律效力上只是“指导性技术文件”，效力低于《通用规范汉字表》。因此《古籍印刷通用字规范字形表》并未广泛推广开来，繁体出版的字形依据仍然是在《通用规范汉字表》的基础上进行调整。

本仓库仍以《通用规范汉字表》为依据，基于[OpenCC](https://github.com/BYVoid/OpenCC)转换引擎，提供从港、台标准以及各种标准和旧字形混杂的“繁体”到《通用规范汉字表》的规范繁体字形的转换方案。从简体到《通用规范汉字表》的规范繁体字形的转换，在Github上已有成熟方案：[OpenCC 简繁转换之通用规范汉字标准](https://github.com/amorphobia/opencc-tonggui)。本仓库亦提供了基于OpenCC原版转换字表和词典、按照《通用规范汉字表》要求修订后的繁⇄简转换字表和词典，以满足基本的繁⇄简双向转换需要。

本仓库同时提供了一个Python转换程序，能够实现Word文档（DOC/DOCX）、文本文件（TXT）和字幕文件（SRT、ASS/SSA、LRC）的繁体字形转换。该程序仍以[OpenCC](https://github.com/BYVoid/OpenCC)作为转换引擎。

## 使用说明
> [!NOTE]
>本仓库的[Releases](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/releases)下已提供“规范繁体字形转换器”的安装包，支持Win10/Win11。Ubuntu/Linux Mint已提供deb格式安装包。您可以直接下载安装，如果你需要了解细节，再阅读以下使用说明。

OpenCC转换的配置文件存于本仓库的“t2gov”文件夹下，使用者应自行拷贝到OpenCC的方案文件夹中，具体可参照OpenCC的说明文档。基于使用者可以进行自定义/编辑转换字表、词典的考虑，“t2gov”下的字表、词典均为txt格式，并未转换为ocd2格式。使用者可以调用OpenCC自行转换为ocd2，转换后应相应编辑json文件令其使用ocd2。

本仓库提供了四种转换方案：繁体转换为规范繁体、只转换繁体旧字形到新字形、繁体转换为简体、简体转换为规范繁体。目前的所有转换方案都适用于OpenCC 1.2.0及以上版本，低于1.2.0版本的OpenCC，请删除字表和词典中的所有注释后再使用。

繁体转换为规范繁体的方案文件为t2gov.json，字表*文件名为TGCharacters.txt，词典文件名为TGPhrases.txt。

>考虑到部分繁体文档是使用内地的输入法软件打出来的，存在不少繁简混杂的情形，因此字表（TGCharacters.txt）第1808行后加入了多组简→规范繁体的转换以改善繁简混杂的状态。如果使用者转换的文档本身就包含简体内容，那么应使用t2gov_keep_simp.json作为方案文件，TGCharacters_keep_simp.txt作为字表。但是命中OpenCC转换词典的简体字仍然会被转换，使用后请注意校对。

只转换繁体旧字形到新字形的方案文件为t2new.json，字表文件名为GovVariants.txt。这个方案会保留大部分异体字不转换。

>考虑到部分繁体文档是使用内地的输入法软件打出来的，存在不少繁简混杂的情形，因此该方案的字表（GovVariants.txt）第390行后也加入了多组简→规范繁体的转换以改善繁简混杂的状态。如果使用者转换的文档本身就包含简体内容，那么应使用t2new_keep_simp.json作为方案文件，GovVariants_keep_simp.txt作为字表。

繁体转换为简体的方案文件为t2s.json，字表文件名为TSCharacters.txt，词典文件名为TSPhrases.txt。

简体转换为规范繁体的方案文件为s2t.json，字表文件名为STCharacters.txt，词典文件名为STPhrases.txt。

“transformer”文件夹下提供了一套模块化的 Python 转换程序，由 `main.py` 作为程序入口统一调度，内部为 `constants.py`（版本常量）、`updater.py`（在线更新检测）、`text_converter.py`（TXT/SRT/ASS/LRC 转换与编码检测）和 `doc_converter.py`（Word 文档转换）四个模块。如果使用者希望自行部署转换程序，可以按以下说明操作。

在 Windows 系统上，使用者需部署好 Python 运行环境。然后打开终端（PowerShell），执行以下命令安装依赖并运行：

```bash
git clone https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards.git
cd OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/transformer
pip install -r requirements.txt
Copy-Item -Path "..\t2gov\*" -Destination "$(python -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc'))")" -Recurse -Force
$dest = python -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc', 'jieba_dict'))"
$null = New-Item -ItemType Directory -Path $dest -Force; Copy-Item -Path "..\jieba\*" -Destination $dest -Recurse -Force
python main.py
```

在 Linux 发行版下，可使用 “transformer-linux” 文件夹下的转换程序。该程序仅支持 docx 文档、txt 文件和字幕文件的繁体字形转换，暂不支持doc文档的转换。使用者需部署好 Python 运行环境，然后打开终端，执行以下命令安装依赖并运行：

```bash
git clone https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards.git
cd OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/transformer-linux
pip install -r requirements.txt
cp -rf ../t2gov/* "$(python3 -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc'))")"
DEST="$(python3 -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc', 'jieba_dict'))")"
mkdir -p "$DEST" && cp -rf ../jieba/* "$DEST"
python3 main.py
```

结巴分词支持词典位于jieba目录下，其中现代汉语分词词典来自[结巴分词仓库](https://github.com/fxsjy/jieba)，古汉语分词默认词典使用了[Dingyuan Wang](https://github.com/gumblex)制作的[jiebazhc](https://github.com/The-Orizon/nlputils)。如需结巴分词功能，请前往OpenCC官方仓库提取（[Windows](https://github.com/BYVoid/OpenCC/releases/download/ver.1.3.0/OpenCC-1.3.0-windows-x64-portable.zip)、[Linux](https://github.com/BYVoid/OpenCC/releases/download/ver.1.3.0/opencc-jieba_1.3.0_amd64.deb)）bin\plugins下的文件复制到你本地的OpenCC目录下（可运行 `pip show opencc` 命令查看OpenCC所在位置）。

> [!NOTE]
>在Windows系统上，部分情况下转换doc文档时会出现错误提示“AttributeError: module ‘win32com.gen_py.00020905-0000-4B30-A977-D214852036FFx0x3x0’ has no attribute ‘CLSIDToClassMap’”。如出现该错误，可尝试删除C:\Users\administrator（注：此处为你的计算机用户名，默认名称为administrator或admin，如有微软账户一般则为微软账户名）\AppData\Local\Temp\gen_py\3.13(注：此处为你安装的Python版本号)下的缓存文件夹00020905-0000-4B30-A977-D214852036FFx0x3x0，再重新运行转换器。如果错误提示代号并非00020905-0000-4B30-A977-D214852036FFx0x3x0，亦可照此操作以排除故障。

## 特别注意
由于《通用规范汉字表》规定的异体—正体映射关系相对简单、不完全符合实际情况，本转换方案依据《现代汉语词典》《辞海》对部分异体字▶正体字转换关系作出了调整。本方案不能视为与《通用规范汉字表》的规定完全一致。

转换字表、词典的底稿是从OpenCC的转换方案修订而来，因此可能存在极少量的用字不符合内地标准、转换存在错误。建议使用者（尤其是出版从业者）应将本方案及其附带的转换工具视为一种便利工具，而不应将本转换方案视为与黑马、方正校对后同等水平的产物。

*特别感谢易建鹏老师、胡馨媚老师、段亚彤老师在字表编制过程中提出的宝贵意见。

## 使用本转换方案的项目

* [opencc-wasm](https://github.com/frankslin/OpenCC) （[npm](https://www.npmjs.com/package/opencc-wasm)、[演示页面](https://opencc.js.org/)）  维护者：[FranksLin](https://github.com/frankslin)

* [OpenCC File Converter（简繁通转换大师）](https://github.com/TerryTian-tech/OpenCC-DocxConverter)  维护者：[TerryTian-tech](https://github.com/TerryTian-tech)

* [regexp-replace-lists-for-TextPro](https://github.com/Fusyong/regexp-replace-lists-for-TextPro)  维护者：[Huang Fusyong](https://github.com/Fusyong)

* [telegram-zh-Hant-CN](https://github.com/soizo/telegram-zh-Hant-CN)  维护者：[SoizoKtantas](https://github.com/soizo)

## Contributors 开源贡献者

由于本仓库同时在Gitee和GitCode平台托管，特此将所有平台的开源贡献者列表如下：

* [TerryTian-tech](https://github.com/TerryTian-tech)

* [FranksLin](https://github.com/frankslin)

## 许可协议

[Apache License 2.0](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/blob/main/LICENSE)

jieba目录下的jieba.dict.utf8来自[结巴分词仓库](https://github.com/fxsjy/jieba)，jieba.dict.ancient.chinese.utf8和jieba.dict.ancient.chinese.traditional.utf8来自[Dingyuan Wang](https://github.com/gumblex)制作的[jiebazhc](https://github.com/The-Orizon/nlputils)。以上文件遵循MIT License开源，特此说明。
