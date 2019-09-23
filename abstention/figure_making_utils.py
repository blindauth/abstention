from __future__ import division, print_function
import numpy as np

#srs: signed rank sum test
#critical values for test statistics here: https://www.oreilly.com/library/view/nonparametric-statistics-a/9781118840429/bapp02.xhtml
def wilcox_srs(vals1, vals2):   
    vals1 = np.array(vals1)
    vals2 = np.array(vals2)
    signed_ranks = ([(1+x[0])*np.sign(x[1]) for x in 
                     enumerate(sorted(vals1-vals2, key=lambda x: abs(x)))])
    sum_positives = sum([x for x in signed_ranks if x > 0]+[1e-7])
    sum_negatives = sum([x for x in signed_ranks if x < 0]+[-1e-7])
    if (np.abs(sum_negatives) < sum_positives):
        return np.abs(sum_negatives)
    else:
        return -np.abs(sum_positives)


def get_ustats_mat(method_to_perfs, method_names, max_ustat=None):
    to_return = np.zeros((len(method_names), len(method_names)))
    for i in range(len(method_names)):
        for j in range(len(method_names)):
            vals1 = method_to_perfs[method_names[i]]  
            vals2 = method_to_perfs[method_names[j]]
            if (np.sum(np.abs(np.array(vals1)-np.array(vals2)))==0):
                if (max_ustat is None):
                    max_ustat = (len(vals1)+1)*(len(vals2)/2.0)
                to_return[i,j] = max_ustat 
            else:
                to_return[i,j] = wilcox_srs(
                            vals1 = vals1,
                            vals2 = vals2)

    return to_return


def get_tied_top_and_worst_methods(ustats_mat, method_names, threshold):
    masked_out_ustats = ((ustats_mat > -threshold)*
                         (ustats_mat < threshold))
    num_triumphs = np.sum(masked_out_ustats, axis=-1)
    sorted_methods_and_numtriumphs = sorted(
        zip(method_names, num_triumps),
        key=lambda x: -x)
    top_method_name, topmethod_numtriumphs = sorted_methods_and_numtriumphs[0]
    #print("top:",top_method_name, top_method_ustats)
    tied_top_methods = [x for x,y in enumerate(num_triumphs)
                        if y==topmethod_numtriumphs]
    worst_method_name, worst_method_numtriumphs = sorted_methods_and_numtriumphs[-1]
    #print("worst:",worst_method_name, worst_method_ustats)
    tied_worst_methods = [x for (x,y) in enumerate(numtriumphs)
                          if y==worst_method_numtriumphs]
    return tied_top_methods, tied_worst_methods
