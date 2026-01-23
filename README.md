# OpenCC-Traditional Chinese to Traditional Chinese (The Chinese Government Standard)
OpenCC开放中文转换 - 将混杂不同标准的繁体字形转换为《通用规范汉字表》（2013，内地现行法定标准）的规范繁体字形
## 项目介绍
2013年国务院颁布了《通用规范汉字表》作为内地实施《中华人民共和国国家通用语言文字法》的配套规范，该表在确定内地简体字规范字形的同时，在“附件1”中收录了与之对应的繁体字形。虽然内地的输入法软件、繁简转换插件并不完全遵照该表的繁体字形，但是该表的字形被视作内地繁体出版的标准进行适用。2021年又有《古籍印刷通用字规范字形表》（GB/Z 40637—2021）颁布，但是该表存在两个重大缺陷：一是没有确立繁体正体字形和异体字形的标准，正体、异体不作区分全部收录；二是《通用规范汉字表》的部分字形，《古籍印刷通用字规范字形表》不收，而《古籍印刷通用字规范字形表》在法律效力上只是推荐性国标，效力低于《通用规范汉字表》。因此《古籍印刷通用字规范字形表》并未广泛推广开来，繁体出版的字形依据仍然是在《通用规范汉字表》的基础上进行调整。

本仓库仍以《通用规范汉字表》为依据，基于[OpenCC](https://github.com/BYVoid/OpenCC)转换引擎，提供从港、台标准以及各种标准和旧字形混杂的“繁体”到《通用规范汉字表》的规范繁体字形的转换方案。从简体到《通用规范汉字表》的规范繁体字形的转换，在Github上已有成熟方案：[OpenCC 简繁转换之通用规范汉字标准](https://github.com/amorphobia/opencc-tonggui)。因此，本仓库只聚焦于繁体▶规范繁体的转换。

本仓库同时提供了一个Python转换程序，能够实现doc文档、docx文档及txt文件的繁体字形转换。该程序仍以OpenCC作为转换引擎。

## 使用说明
> [!NOTE]
>本仓库的[Releases](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/releases)下已提供“规范繁体字形转换器”的安装包，支持Win10/Win11和Win7。Ubuntu/Linux Mint已提供deb格式安装包。您可以直接下载安装，如果你需要了解细节，再阅读以下使用说明。

OpenCC转换的配置文件存于本仓库的“t2gov”文件夹下，使用者应自行拷贝到OpenCC的方案文件夹中，具体可参照OpenCC的说明文档。基于使用者可以进行自定义/编辑转换字表、词典的考虑，“t2gov”下的字表、词典均为txt格式，并未转换为ocd2格式。使用者可以调用OpenCC自行转换为ocd2，转换后应相应编辑json文件令其使用ocd2。

本仓库提供了三种转换方案：繁体转换为规范繁体、只转换繁体旧字形到新字形、繁体转换为简体。目前的所有转换方案都适用于OpenCC 1.2.0及以上版本，低于1.2.0版本的OpenCC，请删除字表和词典中的所有注释后再使用。

繁体转换为规范繁体的方案文件为t2gov.json，字表*文件名为TGCharacters.txt，词典文件名为TGPhrases.txt。

>考虑到部分繁体文档是使用内地的输入法软件打出来的，存在不少繁简混杂的情形，因此字表（TGCharacters.txt）第1667行后加入了多组简→规范繁体的转换以改善繁简混杂的状态。如果使用者转换的文档本身就包含简体内容，那么应使用t2gov_keep_simp.json作为方案文件，TGCharacters_keep_simp.txt作为字表。但是命中OpenCC转换词典的简体字仍然会被转换，使用后请注意校对。

只转换繁体旧字形到新字形的方案文件为t2new.json，字表文件名为GovVariants.txt。这个方案会保留大部分异体字不转换。

>考虑到部分繁体文档是使用内地的输入法软件打出来的，存在不少繁简混杂的情形，因此该方案的字表（GovVariants.txt）第380行后也加入了多组简→规范繁体的转换以改善繁简混杂的状态。如果使用者转换的文档本身就包含简体内容，那么应使用t2new_keep_simp.json作为方案文件，GovVariants_keep_simp.txt作为字表。

繁体转换为简体的方案文件为t2s.json，字表文件名为TSCharacters.txt，词典文件名为TSPhrases.txt。

“transformer”文件夹下提供了一个Python转换程序。以Windows系统上使用为例，使用者在部署好Python环境后，在Powershell里执行 pip install opencc python-docx chardet lxml pywin32 PyQt5 命令，安装依赖。安装成功后，将“t2gov”文件夹下所有文件复制到C:\Users\administrator(注：此处为你的计算机用户名，默认名称为administrator或admin，如有微软账户一般则为微软账户名)\AppData\Local\Programs\Python\Python313(注：此处为你安装的Python版本号，如有多个文件夹取数字最大的那个)\Lib\site-packages\opencc\clib\share\opencc下，再下载“transformer”文件夹里的转换程序并运行，即可实现doc文档、docx文档及txt文件的繁体字形转换。对于想在Windows下使用转换程序但不想体验繁琐的部署、安装流程的使用者，请直接移步[Releases](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards/releases)选择适合你系统环境的包下载安装即可。

在Mac和Linux发行版下，请使用本仓库“transformer(Mac)”下提供的转换程序。该程序仅支持docx文档及txt文件的繁体字形转换，使用前需要使用者先安装部署好Python环境，在终端中执行 pip install opencc python-docx chardet lxml PyQt5 命令安装依赖，然后将本仓库“t2gov”下所有文件复制到Python打包的OpenCC储存转换方案的目录下（先执行pip show opencc命令找到OpenCC包具体所在位置，储存转换方案的位置一般在opencc/clib/share/opencc下，若不是可尝试搜索t2s.json等文件所在位置）。最后下载“transformer”文件夹里的转换程序并运行，即可实现docx文档及txt文件的繁体字形转换。

在Windows系统上部分场景下转换doc文档时会出现错误提示“AttributeError: module ‘win32com.gen_py.00020905-0000-4B30-A977-D214852036FFx0x3x0’ has no attribute ‘CLSIDToClassMap’”。如出现该错误，可尝试删除C:\Users\administrator（注：此处为你的计算机用户名，默认名称为administrator或admin，如有微软账户一般则为微软账户名）\AppData\Local\Temp\gen_py\3.13(注：此处为你安装的Python版本号)下的缓存文件夹00020905-0000-4B30-A977-D214852036FFx0x3x0，再重新运行转换器。如果错误提示代号并非00020905-0000-4B30-A977-D214852036FFx0x3x0，亦可照此操作以排除故障。

## 特别注意
由于《通用规范汉字表》规定的异体—正体映射关系相对简单、不完全符合实际情况，本转换方案依据《现代汉语词典》《辞海》对部分异体字▶正体字转换关系作出了调整。本方案不能视为与《通用规范汉字表》的规定完全一致。

转换字表、词典的底稿是从OpenCC的转换方案修订而来，因此可能存在极少量的用字不符合内地标准、转换存在错误。建议使用者（尤其是出版从业者）应将本方案及其附带的转换工具视为一种便利工具，而不应将本转换方案视为与黑马、方正校对后同等水平的产物。

*特别感谢易建鹏老师、胡馨媚老师、段亚彤老师在字表编制过程中提出的宝贵意见。
