from unitls import get_attr_unitless
from unitls import read, merge_para
from docx import Document
import re


def filter_paragraphs(paragraphs, keywords):
    filtered_paragraphs = []
    for paragraph in paragraphs:
        if any(keyword in paragraph for keyword in keywords):
            filtered_paragraphs.append(paragraph)
    return filtered_paragraphs

def filter_dict(sheet_index):
    """
    这是进行段落和表格过滤时使用的过滤字典，因此为了不漏掉重要的段落或表格，
    对过滤词列表的要求更为宽松，可以添加的随意些
    :param sheet_index:
    :return:
    """
    synonyms = None
    if sheet_index == 0:
        synonyms = {
            "大地坐标X": ["井位坐标，纵(X)", "纵坐标(X)"],
            "大地坐标Y": ["井位坐标，横(Y)", "横坐标（Y）"],
            "A": ["A点", "A靶点"],
            "B": ["B点", "B靶点"],
            "A测深": ["A点", "A靶点"],
            "B测深": ["B点", "B靶点"],
            "A垂深": ["A点垂深", "A靶点垂深"],
            "B垂深": ["B点垂深", "B靶点垂深"],
            "水平段长": ["水平段长度"],
            "井口装置": ["完井井口"],
            "目的层系": ["完钻层位"],
            "Ⅰ类油层": ["1类"],
            "Ⅱ类油层": ["2类"],
            "Ⅲ类油层": ["3类"],
            "方案产能": ["产能"],
            "原始地层压力":["压力"],
            "So_min(%)": ["含油饱和度"],
            "油层温度（℃）": ["温度"],
            "井身结构" : ["套管"],
            "K_min" : ["渗透率"],
            "地层压力系数" : ["压力系数"]

        }
    elif sheet_index == 1:
        synonyms = {
            "前置液比": ["前置液比例"],
            "均砂比(%)": ["平均砂比(%)"],
            "施工正常限压(MPa)": ["施工限压(MPa)"],
            "费用预算": ["压裂费用预算", "合计", "压裂工程累计费用"],
            "设计总液量": ["压裂液总量","设计"],
            "胍胶压裂液滑溜水(m³)":["滑溜水(m³)"],
            "胍胶压裂液原液(m³)":["胍胶液(m³)"],
            "滑溜水、低粘比例(%)":["滑溜水比例(%)"],
            "分段工艺":["分段"],
            "压裂工艺":["压裂方式"],
            "首段射孔工艺":["射孔"],
            "段数":["段"],
            "簇数":["簇"],
            "分段工具":["桥塞"]
            # "":["类型"]
        }
    elif sheet_index == 2:
        synonyms = {
            # "大地坐标X": ["井位坐标，纵(X)"],
            # "大地坐标Y": ["井位坐标，横(Y)"],
            # "水平段长": ["水平段长度"],
            # "井口装置": ["完井井口"],
            # "目的层系": ["完钻层位"],
            # "Ⅰ类油层": ["1类油层"],
            # "Ⅱ类油层": ["2类油层"],
            # "Ⅲ类油层": ["3类油层"],
            # "方案产能": ["产能"]

        }
    elif sheet_index == 3:
        synonyms = {
            # "大地坐标X": ["井位坐标，纵(X)"],
            # "大地坐标Y": ["井位坐标，横(Y)"],
            # "水平段长": ["水平段长度"],
            # "井口装置": ["完井井口"],
            # "目的层系": ["完钻层位"],
            # "Ⅰ类油层": ["1类油层"],
            # "Ⅱ类油层": ["2类油层"],
            # "Ⅲ类油层": ["3类油层"],
            # "方案产能": ["产能"]

        }
    return synonyms


def synonym_dict(sheet_index):
    """
    很多压裂知识是以同义词出现在文档中的，这里准备同义词典，
    该词典加入提示词中，提示大语言模型这些是要提取字段的同义词。
    :return:
    """
    synonyms = None
    if sheet_index == 0:
        synonyms = {
            "大地坐标X": ["井位坐标，纵(X)","纵坐标(X)"],
            "大地坐标Y": ["井位坐标，横(Y)","横坐标（Y）"],
            "A测深(m)": ["A点测深","A靶点测深","A点斜深","A靶点斜深"],
            "B测深(m)": ["B点测深","B靶点测深","B点斜深","B靶点斜深"],
            "A垂深(m)": ["A点垂深","A靶点垂深"],
            "B垂深(m)": ["B点垂深","B靶点垂深"],
            "水平段长(m)": ["水平段长度"],
            "井口装置": ["完井井口"],
            "目的层系": ["完钻层位"],
            "Ⅰ类油层(m)": ["1类油层"],
            "Ⅱ类油层(m)": ["2类油层"],
            "Ⅲ类油层(m)": ["3类油层"],
            "方案产能": ["产能"],
            "杨氏模量_avg（Gpa）":["杨氏模量平均值"],
            "泊松比_avg":["泊松比平均值"],
            "脆性指数(avg)":["脆性指数平均值"],
            "孔隙度(min)":["解释油层孔隙度最小值"],
            "So_min(%)":["含油饱和度最小值"],
            "So_max(%)": ["含油饱和度最大值"],
            "So_avg(%)": ["含油饱和度平均值"],
            "油层温度（℃）":["油藏中部温度","储层温度"],
            "水平油层厚度(m)" : ["钻遇油层","解释油层"],
            "K_min(mD)" : ["渗透率最小值"],
            "K_max(mD)": ["渗透率最大值"],
            "K_avg(mD)": ["渗透率平均值"],
            "地层压力系数" : ["压力系数无因次"]
        }
    elif sheet_index == 1:
        synonyms = {
            "前置液比": ["前置液比例"],
            "均砂比(%)": ["平均砂比"],
            "施工正常限压(MPa)": ["施工限压"],
            "费用预算(万元)":["压裂费用预算","合计","压裂工程累计费用"],
            "设计总液量(m³)":["压裂液总量","压裂液用量总量","总液量","总入井液量"],
            "胍胶压裂液滑溜水(m³)": ["滑溜水(m³)总量/合计"],
            "胍胶压裂液原液(m³)": ["胍胶液总量/合计"],
            "滑溜水、低粘比例(%)": ["滑溜水比例","滑溜液比例"],
            "分段工艺": ["分段"],
            "压裂工艺": ["压裂方式"],
            "均段间距":["平均段间距","段距平均"],
            "均簇间距":["平均簇间距"],
            "段数":["合计段数","总段数"],
            "簇数":["合计簇数","总簇数"],
            "首段射孔工艺": ["第一级射孔"],
            "石英砂(70/140目)":["石英砂(70/140目)总计"],
            "石英砂(40/70目)":["石英砂(40/70目)总计"]
            # "":["类型"]
        }
    elif sheet_index == 2:
        synonyms = {
            # "大地坐标X": ["井位坐标，纵(X)"],
            # "大地坐标Y": ["井位坐标，横(Y)"],
            # "水平段长": ["水平段长度"],
            # "井口装置": ["完井井口"],
            # "目的层系": ["完钻层位"],
            # "Ⅰ类油层": ["1类油层"],
            # "Ⅱ类油层": ["2类油层"],
            # "Ⅲ类油层": ["3类油层"],
            # "方案产能": ["产能"]

        }
    elif sheet_index == 3:
        synonyms = {
            # "大地坐标X": ["井位坐标，纵(X)"],
            # "大地坐标Y": ["井位坐标，横(Y)"],
            # "水平段长": ["水平段长度"],
            # "井口装置": ["完井井口"],
            # "目的层系": ["完钻层位"],
            # "Ⅰ类油层": ["1类油层"],
            # "Ⅱ类油层": ["2类油层"],
            # "Ⅲ类油层": ["3类油层"],
            # "方案产能": ["产能"]

        }
    return synonyms

def value_dict(sheet_index):
    """
    压裂知识有的是文字，有的是范围，有的是数字，大语言模型并不知道字段的取值是什么形式的，
    这里提供要提取字段的取值字典，供大语言模型参考
    :param sheet_index:
    :return:
    """
    values=None
    if sheet_index == 0:
        values = {
            "目的层系": ["P2l22-3","P2l22-3"],
            "平台/单井": ["57A"],
            "井身结构": ["二开","三开"],
            "油层套管": ["139.7mm×12.09mm×TP110V/TP125V"],
            "井口装置": ["KY105/78-65"],
            "人工井底(m)":["4790.4"],
            "固井质量": ["合格","优秀","良好","不合格"],
            "水敏性强、中、弱、无":["强","中","弱","无"],
            "Ⅰ类油层(m)":["300"],
            "Ⅱ类油层(m)":["400"],
            "井型":["水平井"],
            "井别":["采油井","开发井"]
        }
    elif sheet_index == 1:
        values = {
            "压裂工艺": ["桥塞射孔"],
            "分段工具": ["速钻桥塞","可溶桥塞"],
            "首段射孔工艺": ["连油射孔","连续油管传输射孔"],
            "水平井段":["3690.00~5800.00"],
            "支撑剂类型":["70/140+40/70+30/50目石英砂"],
            "压裂液类型":["胍胶+滑溜水"],
            "暂堵剂类型":["颗粒+粉末各一半"],
            "施工排量(m³/min)":["15-18"],
            "石英砂(70/140目)":["468"],
            "30/50目石英砂(m3)":["1350"],

            # "":["类型"]
        }
    elif sheet_index == 2:
        values = {
            # "大地坐标X": ["井位坐标，纵(X)"],
            # "大地坐标Y": ["井位坐标，横(Y)"],
            # "水平段长": ["水平段长度"],
            # "井口装置": ["完井井口"],
            # "目的层系": ["完钻层位"],
            # "Ⅰ类油层": ["1类油层"],
            # "Ⅱ类油层": ["2类油层"],
            # "Ⅲ类油层": ["3类油层"],
            # "方案产能": ["产能"]

        }
    elif sheet_index == 3:
        values = {
            # "大地坐标X": ["井位坐标，纵(X)"],
            # "大地坐标Y": ["井位坐标，横(Y)"],
            # "水平段长": ["水平段长度"],
            # "井口装置": ["完井井口"],
            # "目的层系": ["完钻层位"],
            # "Ⅰ类油层": ["1类油层"],
            # "Ⅱ类油层": ["2类油层"],
            # "Ⅲ类油层": ["3类油层"],
            # "方案产能": ["产能"]

        }
    return values

def clue_dict(sheet_index):
    """
    该字典旨在对专业性较强的压裂知识做更详细的解释
    :param sheet_index:
    :return:
    """
    clues={}
    if sheet_index==0:
        clues={
            "井身结构" : "该字段的取值是文字，形式是一开、二开、三开，判断方法是看该井用了几个套管，如果用了表套和油套，则是二开；用了表套、技套和油套，则是三开。",
            "孔隙度(min)" : "该字段的取值是数字，指的是解释油层孔隙度的最小值，可能会直接给出，也有可能让你从范围中进行提取",
            "孔隙度(max)" : "该字段的取值是数字，指的是解释油层孔隙度的最大值，可能会直接给出，也有可能让你从范围中进行提取",
            "So_min(%)" : "该字段的取值是数字，指的是含油饱和度最小值，可能会直接给出，也可能是给出一个范围，你从范围中提取",
            "So_max(%)": "该字段的取值是数字，指的是含油饱和度最大值，可能会直接给出，也可能是给出一个范围，你从范围中提取",
            'So_avg(%)': '该字段的取值是数字，指的是含油饱和度的平均值，若出现在表中未明确最大最小，一般指均值',
            '水平油层厚度(m)' : '该字段的取值是数字',
            'K_min(mD)' : '该字段的取值是数字，指的是渗透率最小值，可能会直接给出，也可能是给出一个范围，你从范围中提取',
            'K_max(mD)': '该字段的取值是数字，指的是渗透率最大值，可能会直接给出，也可能是给出一个范围，你从范围中提取',
            'K_avg(mD)': '该字段的取值是数字，指的是渗透率平均值',
            '目的层系' : '该字段通常以（Pxlxx-x: xm-xm/xm）形式出现，其中Pxlxx-x就是目的层系'

        }
        pass
    elif sheet_index==1:
        clues={
            "施工排量(m³/min)": "该字段的取值是一个范围，可能需要你从表或段落中提取出最小值和最大值后以范围的形式返回",
            "石英砂(100/200目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "石英砂(70/140目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "石英砂(50/140目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "石英砂(40/70目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "石英砂(30/50目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "石英砂(20/40目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "陶粒(100/200目)":"该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "陶粒(70/140目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "陶粒(50/140目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "陶粒(40/70目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "陶粒(30/50目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "陶粒(20/40目)": "该字段的取值是数字，指的是使用该材料的总量，而不是百分比等，通常位于类似表名为材料统计表的表中或段落中",
            "费用预算(万元)": "该字段通常位于表名位于费用预算表的表中或位于段落中，指的是合计费用",
            "支撑剂类型": "该字段通常位于表名类似支撑剂选择表的表中，或者位于有关支撑剂选择的段落中，指的是使用了哪些材料作为支撑剂",
            "分段工具" : "该字段指的是使用的分段工具的类型，要分清使用的桥塞到底是可溶桥塞还是速钻桥塞"

        }
        pass
    elif sheet_index==2:

        pass
    elif sheet_index==3:

        pass
    return clues

def value_range_dict(sheet_index):

    pass
def expand_keywords(synonyms, questions):
    expand_fields = questions.copy()
    for keyword_list in synonyms.values():
        expand_fields.extend(keyword_list)
    return expand_fields


def filter_by_table_name(tables):
    tables_filtered_fur = []
    # level_pattern = re.compile(r'第.*级')
    level_pattern = re.compile(r'第')
    for table in tables:
        table_name = table.strip().split('\n')[0]
        if not level_pattern.search(table_name):
            tables_filtered_fur.append(table)
    return tables_filtered_fur


def filter(filed2extract, paras, tables, sheet_index=0):
    paras = merge_para(paras)
    synonyms = filter_dict(sheet_index)
    expand_fields = expand_keywords(synonyms, filed2extract)
    paragraphs_filtered = filter_paragraphs(paras, expand_fields)
    tables_filtered = filter_paragraphs(tables, expand_fields)
    tables_filtered_fur = filter_by_table_name(tables_filtered)
    return paragraphs_filtered, tables_filtered_fur


if __name__ == "__main__":
    sheet_index = 3
    sheet_names = ["基础表", "设计表", "施工表", "生产表"]
    all_attr_unitless = get_attr_unitless("水平井压裂数据管理.xlsx", sheet_names)
    question_unitless = all_attr_unitless[sheet_index]
    print(len(question_unitless))
    synonyms = synonym_dict(sheet_index)
    paras, tables = read("1.docx")
    paras, tables = filter(question_unitless, paras, tables, sheet_index)

    # for table in tables:
    #     print(table)
    for para in paras:
        print(para)
    print(len(paras))
    print(len(tables))
    # paras=merge_para(paras)
    # paragraphs_filtered=filter_paragraphs(paras,question)
    # sum_len=0
    # for para in paragraphs_filtered:
    #     print(para)
    #     sum_len+=len(para)
    # print(len(paragraphs_filtered))
    # print(sum_len)
    # #
    # tables_filtered=filter_paragraphs(tables,question)
    # # print(tables_filtered)
    # # for table in tables_filtered:
    # #     print(table)
    # # print(len(tables_filtered))
    # tables_filtered_fur=filter_by_table_name(tables_filtered)
    # # for table in tables_filtered_fur:
    # #     print(table)
    # print(len(tables_filtered_fur))
