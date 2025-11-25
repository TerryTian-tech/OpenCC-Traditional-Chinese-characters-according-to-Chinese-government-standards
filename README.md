# OpenCC-Traditional Chinese to Traditional Chinese (The Chinese Government Standard)
OpenCC开放中文转换 - 将混杂不同标准的繁体字形转换为《通用规范汉字表》（2013，内地现行法定标准）的规范繁体字形
## 项目介绍
2013年国务院颁布了《通用规范汉字表》作为内地实施《中华人民共和国国家通用语言文字法》的配套规范，该表在确定内地简体字规范字形的同时，在附表一中收录了与之对应的繁体字形。虽然内地的输入法软件、繁简转换插件并不完全遵照该表的繁体字形，但是该表的字形被视作内地繁体出版的标准进行适用。2021年又有《古籍印刷通用字规范字形表》（GB/Z 40637—2021）颁布，但是该表存在两个重大缺陷：一是没有确立繁体正体字形和异体字形的标准，正体异体不作区分全部收录；二是《通用规范汉字表》的部分字形，《古籍印刷通用字规范字形表》不收，而《古籍印刷通用字规范字形表》在法律效力上只是推荐性国标，效力低于《通用规范汉字表》。因此《古籍印刷通用字规范字形表》并未广泛推广开来，繁体出版的字形依据仍然是在《通用规范汉字表》的基础上进行调整。

本仓库仍以《通用规范汉字表》为依据，基于[OpenCC](https://github.com/BYVoid/OpenCC)转换引擎，提供从港、台标准以及各种标准和旧字形混杂的“繁体”到《通用规范汉字表》的规范繁体字形的转换方案。从简体到《通用规范汉字表》的规范繁体字形的转换，在Github上已有成熟方案：[OpenCC 简繁转换之通用规范汉字标准](https://github.com/amorphobia/opencc-tonggui)。因此，本仓库只聚焦于繁体▶规范繁体的转换。

本仓库同时提供了一个Python脚本，能够实现doc文档、docx文档及txt文件的繁体字形转换。该脚本仍以OpenCC作为转换引擎。

## 使用说明
OpenCC转换的配置文件存于本仓库的“t2gov”文件夹下，使用者应自行拷贝到OpenCC的方案文件夹中，具体可参照OpenCC的说明文档。字表文件名为TGCharacters.txt，词典文件名为TGPhrases.txt。基于使用者可以进行自定义/编辑转换字表、词典的考虑，“t2gov”下的字表、词典均为txt格式，并未转换为ocd2格式。使用者可以调用OpenCC自行转换ocd2，转换后应相应编辑t2gov.json文件令其使用ocd2。

考虑到部分繁体文档是使用内地的输入法软件打出来的，存在不少繁简混杂的情形，因此字表（TGCharacters.txt）第1480行后加入了简→规范繁体的转换以改善繁简混杂的状态。如果使用者转换的文档本身就包含简体内容，那么应删除字表（TGCharacters.txt）第1480行后的内容。

“transformer”文件夹下提供了一个Python转换脚本。以Windows系统上使用为例，使用者在部署好Python环境后，在Powershell里执行pip install opencc python-docx chardet lxml pywin32命令，安装依赖。安装成功后，将“t2gov”文件夹下所有文件复制到C:\Users\administrator(注：此处为你的计算机用户名，默认名称为administrator或admin，如有微软账户一般则为微软账户名)\AppData\Local\Programs\Python\Python313(注：此处为你安装的Python版本号，如有多个文件夹取数字最大的那个)\Lib\site-packages\opencc\clib\share\opencc下，再下载“transformer”文件夹里的转换脚本并运行，即可实现doc文档、docx文档及txt文件的繁体字形转换。

对于想在Win10/Win11下使用转换脚本但不想体验繁琐的部署、安装流程的使用者，本仓库的[Releases](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/releases)下已提供了一个由pyinstaller打包的免安装运行版本。

在Mac和linux发行版中使用该转换脚本，需要先去除doc文档转换为docx文档以及监视键盘输入esc键的代码部分。

## 特别注意
由于《通用规范汉字表》规定的异体—正体映射关系相对简单、不完全符合实际情况，本转换方案依据《现代汉语词典》《辞海》对部分异体字▶正体字转换关系作出了调整。本方案不能视为与《通用规范汉字表》的规定完全一致。

转换字表、词典的底稿是从OpenCC的转换方案修订而来，因此可能存在极少量的用字不符合内地标准、转换存在错误。建议使用者（尤其是出版从业者）应将本方案及其附带的转换工具视为一种便利工具，而不应将本转换方案视为与黑马、方正校对后同等水平的产物。
