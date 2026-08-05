import docx  # 导入python-docx库
from docx.document import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph


# 判断是否添加</font>，这里如果添加多个会导致TOC目录显示异常eg:1</font>.1 </font>标题</font>2</font>
def exist_font(run):
    if '</font>' in run.text:
        pass
    else:
        run.text = run.text + '</font>'


''' 
    Get_run_format(run)：     修改节段的格式信息
    input:                    实例化节段对象
'''


def Get_run_format(run):
    # TODO: 自定义添加你需要的节段格式转换
    # 判断文本是否加粗
    if run.font.bold == None:
        pass
    else:
        run.text = '<b>' + run.text + '</b>'

    # 判断文本是否为斜体
    if run.font.italic == None:
        pass
    else:
        run.text = '<i>' + run.text + '</i>'

    # 判断文本是否有下划线
    if run.font.underline == None:
        pass
    else:
        run.text = '<u>' + run.text + '</u>'

    # 判断文本是否添加删除线
    if run.font.strike == None:
        pass
    else:
        run.text = '<s>' + run.text + '</s>'

    # 设置文字颜色
    if run.font.color.rgb == None:
        pass
    else:
        exist_font(run)
        run.text = '<font color=#{}>'.format(run.font.color.rgb) + run.text

    # 判断字体是否高亮显示：
    if run.font.highlight_color == None:
        pass
    else:
        '''
        这里我尝试过了下面的语句，根据https://blog.csdn.net/ningmengshuxiawo/article/details/109112540介绍是可以更换成红色的
        <mark style="background:red" >这里是输入的文本</mark>
        但是我自己尝试的时候发现背景色并不能更换，这里就直接用黄色高亮标记
        '''
        run.text = '<mark>' + run.text + '</mark>'

    # 设置字体，默认为楷体
    if run.font.name == None:
        Is_Chi = False  # 判断run.text中是否有中文
        for i in range(len(run.text)):
            if '\u4e00' <= run.text[i] <= '\u9fff':  # 中文字符串unicode范围\u4e001-\u9fff，设置为楷体
                Is_Chi = True
                break
            else:
                continue
        if Is_Chi == True:
            exist_font(run)
            run.text = '<font face="楷体">' + run.text
        else:  # 数字&英文设置为Times New Roman
            exist_font(run)
            run.text = '<font face="Times New Roman">' + run.text
    else:
        exist_font(run)
        run.text = '<font face={}>'.format(run.font.name) + run.text

    # 设置文字大小，默认为3
    if run.font.size == None or run.font.size == 152400:
        font_size = 3  # 默认字体/4号字设置为3
    elif run.font.size < 152400:
        if run.font.size < 95250:
            font_size = 1  # 比六号字小的设置为1
        else:
            font_size = 2  # 介于六号字到4号字之间的设置为2
    else:
        if run.font.size == 177800:
            font_size = 4  # 四号字置为4
        elif run.font.size < 203200:
            font_size = 5  # 介于四号字到三号字之间的设置为5
        elif run.font.size < 279400:
            font_size = 6  # 介于三号字到二号字之间的设置为6
        else:
            font_size = 7  # 大于二号字之间的设置为7
    exist_font(run)
    run.text = '<font size={}>'.format(font_size) + run.text


'''
    Get_paragraph_format(paragraph)：     修改段落的格式信息
    input:                                实例化段落对象
'''


def Get_paragraph_format(paragraph):
    # TODO: 自定义添加你需要的段落格式转换

    # #行间距=行高-字体大小
    # if paragraph.paragraph_format.line_spacing!=None:
    #     for run in paragraph.runs:
    #         exist_font(run)
    #         run.text = '<font style="line-height:{};">'.format(paragraph.paragraph_format.line_spacing) + run.text
    # else:
    #     for run in paragraph.runs:
    #         exist_font(run)
    #         run.text = '<font style="line-height:1.0;">' + run.text

    # #段前间距
    # if paragraph.paragraph_format.space_before!=None:
    #     pass
    # else:
    #     pass

    # 读取段落标题 docx中最高支持9级标题，但Markdown最高只支持６级标题
    # paragraph.style.name 返回值“Heading 标题等级数字”
    if 'Heading' in paragraph.style.name:  # 判断段落是否为标题
        level = eval(paragraph.style.name[-1])
        for run in paragraph.runs:
            i = run.text.index('size=') + 5  # 查询标题中设置标题字号的文本位置
            run.text = run.text[:i] + '{}'.format(7 - level) + run.text[i + 1:]  # 1级标题文字大小为6
        paragraph.text = '#' * level + ' ' + paragraph.text

    # 首行缩进
    # 首行缩进的单位支持Pt、Cm、Mm、Inches等，如果想要缩进几个字符，需要自己进行转换，因为不同字号字符占用的磅数是不同的（五号字体 = 10.5pt = 3.70mm = 14px = 0.146inch）
    if paragraph.paragraph_format.first_line_indent != None:  # 判断段落是否使用首行缩进
        paragraph.text = '&#8195;' * 2 + paragraph.text


'''
    Get_table_format(table):获取表格格式
    input：一个table对象
    output：table对象转变为html格式的字符串
'''

def Get_table_format(table):
    table_text = '<table>\n'

    # 按行/列将cell地址存入二维列表中
    row_cells, col_cells = [], []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append(cell)
        row_cells.append(cells)
    for col in table.columns:
        cells = []
        for cell in col.cells:
            cells.append(cell)
        col_cells.append(cells)

    row_temp, col_temp = [], []
    for i in range(len(table.rows)):
        table_text = table_text + '<tr>\n'
        for j in range(len(table.columns)):
            col_counts = row_cells[i].count(row_cells[i][j])  # 确定行中重复地址数目以确定合并数量
            row_counts = col_cells[j].count(col_cells[j][i])  # 确定列中重复地址数目以确定合并数量
            if row_cells[i][j] not in row_temp and col_cells[j][i] not in col_temp:  # 行列地址值去重
                if col_counts == 1 and row_counts == 1:  # 单元格没有合并
                    table_text = table_text + '<td>' + table.rows[i].cells[j].text.replace("\n", "") + '</td>'
                elif col_counts != 1 and row_counts == 1:  # 横向合并
                    table_text = table_text + '<td colspan={}>'. \
                        format(col_counts) + table.rows[i].cells[j].text.replace("\n", "") + '</td>'
                elif col_counts == 1 and row_counts != 1:  # 纵向合并
                    table_text = table_text + '<td rowspan={}>'. \
                        format(row_counts) + table.rows[i].cells[j].text.replace("\n", "") + '</td>'
                else:  # 横纵同时合并
                    table_text = table_text + '<td colspan={0} rowspan={1}>'. \
                        format(col_counts, row_counts) + table.rows[i].cells[j].text.replace("\n", "") + '</td>'
            row_temp.append(row_cells[i][j])
            col_temp.append(col_cells[j][i])
        table_text = table_text + '\n</tr>\n'
    table_text = table_text + '</table>\n'

    return table_text


def iter_block_items(parent):
    """
    Yield each paragraph and table child within *parent*, in document order.
    Each returned value is an instance of either Table or Paragraph. *parent*
    would most commonly be a reference to a main Document object, but
    also works for a _Cell object, which itself can contain paragraphs and tables.
    """
    if isinstance(parent, Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError("something's not right")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)

def read(file_path):
    doc = docx.Document(file_path)  # 打开.docx文件


    tables=[]
    paragraphs=[]
    for block in iter_block_items(doc):
        # block.style.name可以直接返回：heading 1、normal、normal table
        if isinstance(block, Table):
            text = paragraphs[-1] + "\n"
            paragraphs.remove(paragraphs[-1])
            table_text = text+Get_table_format(block)
            tables.append(table_text)
        elif isinstance(block, Paragraph):
            paragraphs.append(block.text)
    return paragraphs,tables


if __name__ == '__main__':

    paragraphs,tables=read("J10019_H井桥塞-射孔联作分段压裂设计-22级陈亮-已批.docx")
    for paragraph in paragraphs:
        print(paragraph)
    for table in tables:
        print(table)
