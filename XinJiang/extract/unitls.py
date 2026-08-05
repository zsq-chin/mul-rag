import pandas as pd
from read_doc import read
from pathlib import Path

def get_file_name(dir_path):
    docx_files = [file.name for file in Path(dir_path).rglob("*.docx")]
    # print(docx_files)  # 输出如：['file1.docx', 'file2.docx']
    return docx_files

def get_attr(excel_path,sheet_names):
    # 读取 Excel 文件

    all_attr=[]
    for sheet_name in sheet_names:
        df = pd.read_excel(excel_path, header=None,sheet_name=sheet_name)



        # 获取第二行（属性名）和第三行（单位）
        names = df.iloc[1].values  # 第二行，可能是小属性名
        units = df.iloc[2].values  # 第三行，可能是单位

        # 存储小属性的字典
        small_attributes = {}

        # 当前列的位置
        current_col = 0
        attributes=[]
        # 遍历第二行和第三行
        for i, name in enumerate(names):
            # 判断是否是合并单元格的情况（这取决于第二行的内容是否是实际的属性名）
            # 如果是合并单元格的小属性（例如没有单位）
            if isinstance(name, str) and isinstance(units[i], str):
                small_attributes[name] = {'unit': units[i]}
                attributes.append(name+units[i])
            else:
                # 可能是普通的小属性
                small_attributes[name] = {'unit': units[i]}
                attributes.append(name)

            current_col += 1
        all_attr.append(attributes)
    return all_attr
def get_attr_unitless(excel_path,sheet_names):
    # 读取 Excel 文件

    all_attr=[]
    for sheet_name in sheet_names:
        df = pd.read_excel(excel_path, header=None,sheet_name=sheet_name)



        # 获取第二行（属性名）和第三行（单位）
        names = df.iloc[1].values  # 第二行，可能是小属性名
        units = df.iloc[2].values  # 第三行，可能是单位

        # 存储小属性的字典
        small_attributes = {}

        # 当前列的位置
        current_col = 0
        attributes=[]
        # 遍历第二行和第三行
        for i, name in enumerate(names):
            # 判断是否是合并单元格的情况（这取决于第二行的内容是否是实际的属性名）
            # 如果是合并单元格的小属性（例如没有单位）
            if isinstance(name, str) and isinstance(units[i], str):
                small_attributes[name] = {'unit': units[i]}
                attributes.append(name)
            else:
                # 可能是普通的小属性
                small_attributes[name] = {'unit': units[i]}
                attributes.append(name)

            current_col += 1
        all_attr.append(attributes)
    return all_attr


def merge_para(paragraphs):
    para_merged=""
    paras=[]
    for para in paragraphs:
        para_merged+=para
        if len(para_merged)>=600:
           paras.append(para_merged)
           para_merged=""
    return paras


if __name__=="__main__":
    sheet_names=["基础表","设计表","施工表","生产表"]
    all_attr=get_attr("水平井压裂数据管理.xlsx",sheet_names)
    count=0
    for index,attrs in enumerate(all_attr):
        num=len(attrs)
        print(f"{sheet_names[index]}要提取的字段共有{num}个")
        count+=num
        print(attrs)

    print(f"总共要提取的字段共{count}个")
    get_file_name(".")