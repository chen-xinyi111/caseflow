"""Audit actual inputs; preserve raw splits and disclose repeated controls."""
def analysis_rows(rows):
    result=[]
    for row in rows:
        if row['kind']=='investigation' and row['input'].get('target')=='unconnected':
            result.append(row|{'original_split':row['split'],'split':'control','source_group':'shared-unconnected-control'})
        else:result.append(dict(row))
    return result
