# utils imports
import time
import numpy as np
import pandas as pd
import random
from collections import deque
from math import fabs, floor
import os
import matplotlib.pyplot as plt

os.environ["KERAS_BACKEND"] = "plaidml.bridge.keras"

# Gym imports
import gym
from gym import Env
from gym.spaces import Discrete, Box


# Keras imports
import tensorflow as tf
from keras.models import Sequential
from keras.layers import Dense, Dropout
from keras.optimizers import Adam
# from rl.agents import DQNAgent
# from rl.policy import BoltzmannQPolicy
# from rl.memory import SequentialMemory

# multthreading imports
import threading
from multiprocessing import Pool

# client/server imports
from http import client
import socket

# cplex imports
from docplex.mp.model import Model
import cplex as CPX
import cplex.callbacks as CPX_CB
from cplex.callbacks import SolutionStrategy, MIPCallback, BranchCallback


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class BranchCB(CPX_CB.BranchCallback):

    def init(self, _lista):
        self.nodes_to_process = _lista
        self.times_called = 0
        self.report_count = 0
        self.branches_count = 0 
    def __call__(self):
        # Counter of how many times the callback was called
        self.times_called += 1
        br_type = self.get_branch_type()
        #if br_type == self.branch_type.SOS1 or br_type == self.branch_type.SOS2:
        #    return
        # Getting information about state of node and tree
        x = self.get_values()

        objval = self.get_objective_value()
        best_objval = self.get_best_objective_value()

        obj    = self.get_objective_coefficients()
        feas   = self.get_feasibilities()

        node_id = self.get_node_ID() # node id of the current node
        incumbentval = self.get_incumbent_objective_value() # value of the incumbent solution
        cutoff = self.get_cutoff() # cutoff value
        
        # client_socket.send((str(objval)+';'+str(best_objval)+';'+str(incumbentval)+';'+str(cutoff)).encode())  # send message
        client_socket.send(("proceed").encode())  # send message
        cplex_m_global.data = np.array([(objval - incumbentval)/incumbentval])
        _ = client_socket.recv(1024).decode()  # receive response
        data = EnvCustom.data

        maxobj = -CPX.infinity
        maxinf = -CPX.infinity
        bestj  = -1
        branching = data


        branch_d = -1
        branch_u = -1
        # print('branching type: ', branching, '-- Times called: ', self.times_called)
        if branching == 0: # Branch on variable with most fractional value (nearest to 0.5)
            # MOST FRACTIONAL BRANCHING
            for j in range(len(x)):
                if feas[j] == self.feasibility_status.infeasible:
                    xj_inf = x[j] - floor(x[j])
                    if xj_inf > 0.5:
                        xj_inf = 1.0 - xj_inf
                        
                    if (xj_inf >= maxinf and (xj_inf > maxinf or fabs(obj[j]) >= maxobj)):
                        bestj = j
                        maxinf = xj_inf
                        maxobj = fabs(obj[j])

        elif branching == 1: # Branch on random variable
            feasible_vars = [i for i in range(len(x)) if feas[i] == self.feasibility_status.infeasible]
            if len(feasible_vars) == 0:
                return
            bestj = int(np.random.choice(feasible_vars))
            if bestj < 0:
                return

        elif branching == 2:
            cplex_m_global.parameters.mip.strategy.variableselect = -1 # Branch on variable with minimum infeasibility
        elif branching == 3:
            cplex_m_global.parameters.mip.strategy.variableselect = 1 # Branch on variable with maximum infeasibility
        elif branching == 4:
            cplex_m_global.parameters.mip.strategy.variableselect = 2 # Branch based on pseudo costs
        elif branching == 5:
            cplex_m_global.parameters.mip.strategy.variableselect = 3 # Strong branching
        elif branching == 6:
            cplex_m_global.parameters.mip.strategy.variableselect = 4 # Branch based on pseudo reduced costs
        elif branching == 99:
           cplex_m_global.parameters.mip.strategy.variableselect = 0 # Automatic: let CPLEX choose variable to branch on; default


        # Making the branching  
        if branching == 0 or branching == 1:
            xj_lo = floor(x[bestj])

            branch_d = self.make_branch(objval, variables = [(bestj, "U", xj_lo)], node_data = {'var':bestj, 'parent': self.get_node_ID(),'var_val_base':xj_lo, 'type':"DOWN"})
            branch_u = self.make_branch(objval, variables = [(bestj, "L", xj_lo + 1)], node_data = {'var':bestj, 'parent': self.get_node_ID(),'var_val_base':xj_lo, 'type':"UP"})
        else:
            branch_d = self.make_branch(objval, node_data={'parent':self.get_node_ID()})
            branch_u = self.make_branch(objval, node_data={'parent':self.get_node_ID()})


        self.nodes_to_process.append({'node_id':     node_id, 
                                    'branchd_d':     branch_d[0], 
                                    'branch_u':      branch_u[0], 
                                    'best_objval':   best_objval, # best objective function value, i.e. the best known dual bound
                                    'objval':        objval,  # function value at the current node
                                    'incumbentval':  incumbentval, # value of the incumbent/current solution - i.e. the best known primal bound
                                    'cutoff':        cutoff, # cutoff value, i.e. the best known primal bound + 1
                                    'gap':           (objval - incumbentval)/incumbentval}) 
        
        self.report_count+=1
        if self.report_count % 500 == 0 and self.report_count > 0:
            pd.DataFrame(self.nodes_to_process).to_csv('nodes_to_process.csv')  

class NodeCB(CPX_CB.NodeCallback):

    def init(self, _lista):
        self.nodes_to_process = _lista
        self.times_called = 0

    def __call__(self):
        # Counter of how many times the callback was called
        self.times_called += 1

class MIPInfoCB(CPX_CB.MIPInfoCallback):
    
    def init(self, _lista):
        self.nodes_to_process = _lista
        self.times_called = 0
    
    def __call__(self):
        # Counter of how many times the callback was called
        self.times_called += 1

def init_cplex_model():

    instances = 1
    select_instance = 1 # np.random.randint(0, instances+1)
    if select_instance == 0:
        # Instance no.1
        w = [4, 2, 5, 4, 5, 1, 3, 5]
        v = [10, 5, 18, 12, 15, 1, 2, 8]
        C = [15]
        K = 1
    if select_instance == 1:
        # Instance no.2
        v = [100, 94, 506, 416, 992, 649, 237, 457, 815, 446, 422, 791, 359, 667, 598, 7, 544, 334, 766, 994, 893, 633, 131, 428, 700, 617, 874, 720, 419, 794, 196, 997, 116, 908, 539, 707, 569, 537, 931, 726, 487, 772, 513, 81, 943, 58, 303, 764, 536, 724, 789, 479, 142, 339, 641, 196, 494, 66, 824, 208, 711, 800, 314, 289, 401, 466, 689, 833, 225, 244, 849, 113, 379, 361, 65, 486, 686, 286, 889, 24, 491, 891, 90, 181, 214, 17, 472, 418, 419, 356, 682, 306, 201, 385, 952, 500, 194, 737, 324, 992, 224]
        w = [995, 485, 326, 248, 421, 322, 795, 43, 845, 955, 252, 9, 901, 122, 94, 738, 574, 715, 882, 367, 984, 299, 433, 682, 72, 874, 138, 856, 145, 995, 529, 199, 277, 97, 719, 242, 107, 122, 70, 98, 600, 645, 267, 972, 895, 213, 748, 487, 923, 29, 674, 540, 554, 467, 46, 710, 553, 191, 724, 730, 988, 90, 340, 549, 196, 865, 678, 570, 936, 722, 651, 123, 431, 508, 585, 853, 642, 992, 725, 286, 812, 859, 663, 88, 179, 187, 619, 261, 846, 192, 261, 514, 886, 530, 849, 294, 799, 391, 330, 298, 790]
        C = [100, 100, 100, 100, 100]
        K = 5
    if select_instance == 2:
        # Instance no.3
        v = [585, 194, 426, 606, 348, 516, 521, 1092, 422, 749, 895, 337, 143, 557, 945, 915, 1055, 546, 352, 522, 109, 891, 1001, 459, 222, 767, 194, 698, 838, 107, 674, 644, 815, 434, 982, 866, 467, 1094, 1084, 993, 399, 733, 533, 231, 782, 528, 172, 800, 974, 717, 238, 974, 956, 820, 245, 519, 1095, 894, 629, 296, 299, 1097, 377, 216, 197, 1008, 819, 639, 342, 807, 207, 669, 222, 637, 170, 1031, 198, 826, 700, 587, 745, 872, 367, 613, 1072, 181, 995, 1043, 313, 158, 848, 403, 587, 864, 1023, 636, 129, 824, 774, 889, 640, 579, 654, 242, 567, 439, 146, 741, 810, 296, 653, 594, 291, 166, 824, 924, 830, 308, 1088, 811, 190, 900, 440, 414, 649, 389, 296, 501, 965, 566, 778, 789, 670, 933, 1036, 325, 822, 344, 751, 949, 223, 213, 531, 479, 608, 461, 685, 165, 953, 586, 742, 786, 1092, 386, 825, 989, 386, 124, 912, 591, 959, 991, 763, 190, 188, 281, 279, 314, 287, 117, 719, 572, 361, 518, 946, 519, 292, 456, 361, 782, 614, 406, 986, 301, 630, 485, 949, 1052, 394, 600, 899, 294, 491, 837, 430, 424, 398, 1092, 890, 324, 375, 360, 926, 197, 172, 310, 966, 749, 1051, 1019, 848, 163, 785, 1058, 1056, 904, 664, 618, 283, 528, 500, 637, 821, 446, 307, 253, 423, 1071, 711, 762, 216, 297, 209, 191, 895, 629, 443, 226, 962, 847, 785, 569, 110, 870, 981, 1034, 1084, 823, 503, 995, 460, 668, 549, 272, 641, 1058, 372, 483, 977, 408, 459, 1070, 807, 683, 408, 148, 870, 1030, 130, 669, 308, 103, 411, 120, 200, 709, 1039, 987, 522, 925, 885, 1030, 470, 1004, 1089, 341, 1069, 479, 243, 476, 1072, 1062, 128, 989, 161, 543, 738, 316, 448, 438, 447, 260, 166, 506, 491, 259, 738, 131, 395, 304, 926, 520, 296, 253, 549, 525, 955, 431, 243, 665, 587, 938, 240, 109, 664, 1018, 715, 633, 235, 332, 664, 1057, 460, 691, 893, 676, 263, 846, 959, 477, 860, 958, 811, 186, 762, 534, 259, 658, 760, 379, 368, 940, 1048, 835, 415, 674, 776, 226, 441, 1012, 789, 839, 994, 921, 806, 725, 590, 1017, 578, 301, 771, 1093, 1032, 249, 999, 152, 337, 859, 287, 367, 572, 356, 872, 883, 198, 217, 1006, 616, 1011, 280, 735, 125, 325, 480, 923, 812, 264, 366, 443, 316, 832, 548, 602, 641, 840, 764, 676, 1054, 712, 826, 1002, 872, 554, 631, 511, 1043, 1073, 850, 803, 427, 950, 1017, 177, 105, 320, 213, 902, 1013, 503, 891, 281, 1098, 110, 959, 625, 445, 1019, 531, 768, 775, 627, 933, 562, 538, 391, 623, 705, 1016, 557, 520, 505, 215, 517, 760, 379, 361, 785, 872, 696, 488, 407, 864, 324, 943, 422, 306, 940, 507, 1075, 739, 501, 952, 191, 642, 427, 160, 430, 857, 282, 182, 703, 737, 893, 193, 715, 714, 833, 236, 964, 287, 116, 202, 963, 1072, 1087, 263, 406, 601, 134, 577, 940, 592, 800, 221, 806, 180, 887, 238, 205, 760, 934, 329, 898, 927, 410, 548, 709, 330, 790, 932, 661, 589, 679, 686, 160, 391, 488, 765, 409, 760, 507, 802, 300, 253, 413, 706, 1070, 223, 133, 353, 373, 809, 377, 932, 1097, 208, 140, 988, 327, 581, 960, 383, 1040, 958, 708, 384, 1090, 811, 690, 950, 906, 339, 152, 1043, 901, 295, 864, 195, 810, 510, 486, 764, 693, 1000, 150, 212, 594, 1063, 256, 854, 1036, 558, 1065, 119, 186, 325, 823, 669, 284, 197, 968, 294, 440, 854, 512, 702, 587, 161, 309, 795, 446, 447, 960, 819, 407, 200, 195, 197, 921, 943, 1041, 845, 921, 386, 687, 592, 301, 216, 765, 631, 627, 524, 912, 353, 524, 526, 559, 513, 443, 1080, 936, 391, 606, 933, 279, 586, 1016, 109, 530, 675, 696, 1022, 908, 245, 369, 997, 612, 504, 911, 842, 785, 828, 667, 369, 876, 203, 955, 845, 818, 135, 953, 879, 197, 171, 246, 180, 846, 942, 115, 228, 1065, 1041, 612, 327, 580, 130, 1058, 442, 765, 705, 291, 631, 1064, 464, 615, 459, 706, 967, 922, 920, 636, 413, 793, 307, 119, 1011, 435, 408, 201, 530, 1022, 785, 394, 741, 1010, 213, 510, 241, 350, 790, 646, 800, 829, 659, 709, 581, 820, 603, 1076, 866, 859, 107, 898, 982, 174, 959, 748, 282, 399, 525, 885, 642, 946, 783, 490, 953, 997, 1038, 516, 189, 937, 724, 347, 281, 393, 978, 244, 1033, 1038, 680, 567, 352, 580, 307, 1055, 758, 765, 120, 126, 1054, 664, 447, 549, 859, 126, 546, 621, 114, 401, 757, 421, 810, 713, 1080, 488, 288, 979, 736, 1090, 303, 288, 532, 1063, 229, 920, 730, 400, 609, 922, 337, 355, 473, 377, 938, 253, 495, 500, 792, 327, 203, 618, 318, 547, 484, 415, 1031, 913, 496, 424, 489, 602, 740, 399, 387, 880, 397, 476, 1077, 616, 612, 312, 319, 429, 655, 910, 498, 586, 389, 585, 898, 516, 698, 1052, 149, 994, 672, 458, 797, 121, 870, 747, 825, 144, 642, 783, 314, 461, 987, 914, 145, 331, 194, 108, 382, 265, 182, 596, 419, 799, 726, 952, 246, 559, 641, 635, 974, 414, 825, 1044, 877, 406, 654, 559, 308, 737, 887, 547, 1076, 508, 1012, 207, 544, 1062, 103, 665, 401, 507, 264, 957, 537, 417, 752, 198, 864, 918, 141, 700, 837, 388, 276, 1002, 236, 667, 228, 290, 494, 849, 335, 1008, 142, 376, 924, 848, 784, 692, 1079, 493, 668, 463, 467, 263, 152, 1083, 742, 279, 435, 116, 226, 578, 230, 359, 683, 968, 229, 395, 852, 696, 267, 171, 956, 722, 740, 964, 870, 744, 531, 105, 295, 465, 548, 529, 801, 164, 1052, 954, 980, 870, 268, 188, 179, 257, 1061, 468, 572, 521, 317, 819, 809, 371, 247, 1075, 396, 916, 1009, 1062, 864, 635, 351, 538, 978, 796, 853, 981, 670, 418, 353, 145, 766, 152, 1031, 596, 533, 413, 910, 238, 778, 434, 223, 1088, 312, 608, 166, 870, 306, 851, 850, 880, 164, 541, 566, 307, 437, 616, 610, 774, 896, 508, 275, 1020, 537, 673, 1038, 734, 948, 978, 661, 1090, 818, 926, 1048, 796, 749, 900, 669, 511, 763, 522, 682, 807, 943, 434, 302, 1086, 802, 594, 679, 250, 825, 662, 903, 480, 440, 932, 256, 1079, 932, 476, 702, 611, 334, 514, 788, 260, 284, 1082, 1078, 338, 513, 590, 733, 999, 685, 351, 706, 626, 1063, 997, 450, 139, 342, 757, 639, 552, 839, 638, 367, 931, 265, 560, 277, 285, 820, 1034, 902, 540, 744, 590, 182, 268, 898, 171, 297, 1077, 273, 558, 181, 304, 335, 903, 212, 239, 203, 499, 368, 520, 979, 1091, 743, 678, 206, 1072, 222, 277, 544, 498, 362, 448, 791, 596, 1097, 376, 546, 925, 271, 714, 649, 221, 381, 830, 311, 441, 212, 384, 508, 798, 830, 117, 463, 646, 189, 772, 705, 1074, 1038, 966, 268, 477, 805, 659, 147, 440, 500, 627, 315, 879, 214, 861, 151, 583, 981, 192, 649, 490, 779, 683, 428, 761, 350, 491, 303, 436, 581, 761, 852, 225, 807, 189, 182, 375, 163, 459, 760, 278, 121, 880, 387, 142, 319, 105, 600, 480, 920, 872, 195, 916, 144, 109, 848, 584, 778, 645, 376, 689, 578, 257, 763, 1001, 694, 877, 857, 174, 1047, 347, 548, 155, 670, 579, 413, 885, 1081, 1058, 895, 780, 652, 493, 489, 1044, 380, 696, 964, 719, 736, 711, 612, 952, 992, 383, 284, 399, 1047, 866, 714, 412, 725, 926, 249, 826, 477, 833, 574, 905, 118, 779, 762, 503, 516, 1064, 101, 437, 630, 641, 852, 578, 122, 469, 1081, 1046, 877, 921, 1057, 933, 375, 937, 684, 590, 682, 137, 221, 694, 1051, 229, 768, 358, 218, 346, 419, 462, 996, 855, 621, 604, 1048, 171, 348, 277, 574, 276, 451, 903, 154, 337, 255, 777, 760, 133, 971, 215, 586, 697, 394, 869, 525, 636, 345, 1056, 482, 441, 471, 284, 808, 334, 644, 669, 894, 316, 487, 637, 838, 184, 620, 910, 381, 757, 602, 133, 191, 259, 603, 620, 1024, 768, 116, 387, 133, 329, 444, 474, 449, 574, 706, 772, 996, 1015, 695, 611, 541, 223, 202, 669, 152, 299, 754, 496, 810, 815, 534, 159, 345, 710, 136, 1017, 761, 1027, 513, 370, 113, 217, 470, 616, 500, 315, 295, 316, 1084, 337, 576, 715, 264, 599, 505, 577, 774, 860, 296, 385, 708, 156, 1007, 1066, 632, 722, 151, 321, 816, 294, 427, 191, 980, 447, 810, 352, 367, 573, 500, 616, 476, 485, 709, 457, 420, 1058, 296, 765, 638, 892, 1035, 164, 504, 520, 200, 771, 226, 1068, 955, 977, 194, 277, 831, 623, 353, 239, 108, 252, 493, 832, 643, 346, 448, 665, 418, 840, 589, 1002, 122, 117, 1098, 1073, 149, 398, 481, 711, 110, 132, 529, 111, 321, 437, 250, 236, 817, 989, 630, 622, 432, 905, 1042, 128, 665, 1031, 316, 857, 161, 709, 270, 644, 972, 1005, 744, 695, 308, 346, 780, 213, 797, 275, 739, 256, 779, 344, 1061, 983, 461, 365, 902, 983, 459, 562, 939, 585, 818, 372, 380, 304, 352, 1084, 380, 509, 202, 498, 715, 1009, 396, 470, 581, 982, 1056, 314, 330, 866, 359, 135, 1022, 521, 806, 371, 372, 470, 256, 948, 652, 150, 585, 1076, 636, 370, 170, 896, 1002, 1063, 552, 479, 135, 1091, 1047, 651, 478, 997, 157, 407, 452, 521, 769, 174, 802, 488, 241, 659, 558, 1080, 563, 715, 856, 781, 987, 331, 432, 307, 628, 918, 756, 431, 354, 1079, 887, 545, 595, 448, 470, 824, 110, 501, 300, 746, 427, 768, 117, 342, 474, 402, 855, 961, 1080, 930, 476, 177, 108, 541, 132, 689, 560, 1090, 904, 289, 123, 968, 969, 1096, 1043, 463, 815, 337, 728, 520, 173, 665, 299, 314, 138, 433, 309, 826, 427, 275, 1012, 158, 899, 725, 472, 166, 1076, 269, 220, 575, 767, 304, 288, 190, 128, 596, 804, 101, 749, 574, 846, 549, 797, 352, 812, 432, 389, 387, 725, 289, 1053, 989, 562, 163, 402, 478, 179, 306, 141, 997, 923, 330, 1008, 482, 765, 675, 136, 160, 832, 629, 909, 577, 166, 390, 391, 189, 691, 1061, 683, 561, 352, 802, 579, 632, 630, 780, 324, 153, 888, 742, 409, 1100, 466, 991, 452, 922, 502, 252, 645, 424, 213, 733, 1067, 367, 554, 481, 823, 372, 114, 606, 120, 519, 408, 561, 744, 626, 813, 1078, 582, 568, 436, 829, 389, 895, 596, 681, 426, 483, 630, 326, 267, 521, 622, 486, 930, 373, 191, 584, 216, 776, 847, 207, 495, 329, 1058, 1002, 856, 1062, 362, 205, 803, 184, 611, 1091, 581, 801, 549, 789, 988, 755, 699, 700, 406, 638, 592, 599, 903, 981, 545, 1081, 416, 573, 989, 483, 885, 779, 865, 673, 591, 689, 689, 580, 224, 245, 841, 724, 998, 1023, 825, 1015, 782, 332, 231, 1074, 498, 835, 1006, 778, 853, 740, 412, 744, 890, 312, 401, 223, 1045, 353, 547, 1043, 297, 118, 127, 1016, 673, 427, 689, 740, 1034, 715, 309, 570, 629, 755, 999, 388, 115, 501, 692, 940, 192, 733, 675, 807, 914, 524, 454, 782, 227, 294, 474, 955, 666, 290, 694, 828, 1073, 278, 745, 375, 703, 188, 812, 467, 132, 644, 398, 838, 661, 1063, 1006, 560, 726, 366, 243, 827, 604, 133, 936, 542, 461, 728, 531, 484, 654, 344, 124, 582, 516, 865, 835, 546, 343, 347, 554, 1013, 1032, 660, 594, 628, 748, 694, 457, 1035, 696, 562, 888, 721, 636, 760, 859, 664, 300, 402, 666, 1036, 841, 692, 317, 824, 319, 218, 932, 399, 919, 351, 587, 229, 573, 196, 341, 267, 164, 269, 616, 481, 957, 1076, 318, 867, 590, 937, 604, 507, 807, 303, 442, 487, 575, 835, 275, 806, 886, 435, 271, 140, 332, 921, 988, 231, 966, 259, 1076, 1078, 353, 153, 719, 949, 742, 578, 560, 119, 318, 330, 318, 553, 767, 436, 1049, 1031, 189, 688, 588, 549, 668, 666, 626 ]
        w = [485, 94, 326, 506, 248, 416, 421, 992, 322, 649, 795, 237, 43, 457, 845, 815, 955, 446, 252, 422, 9, 791, 901, 359, 122, 667, 94, 598, 738, 7, 574, 544, 715, 334, 882, 766, 367, 994, 984, 893, 299, 633, 433, 131, 682, 428, 72, 700, 874, 617, 138, 874, 856, 720, 145, 419, 995, 794, 529, 196, 199, 997, 277, 116, 97, 908, 719, 539, 242, 707, 107, 569, 122, 537, 70, 931, 98, 726, 600, 487, 645, 772, 267, 513, 972, 81, 895, 943, 213, 58, 748, 303, 487, 764, 923, 536, 29, 724, 674, 789, 540, 479, 554, 142, 467, 339, 46, 641, 710, 196, 553, 494, 191, 66, 724, 824, 730, 208, 988, 711, 90, 800, 340, 314, 549, 289, 196, 401, 865, 466, 678, 689, 570, 833, 936, 225, 722, 244, 651, 849, 123, 113, 431, 379, 508, 361, 585, 65, 853, 486, 642, 686, 992, 286, 725, 889, 286, 24, 812, 491, 859, 891, 663, 90, 88, 181, 179, 214, 187, 17, 619, 472, 261, 418, 846, 419, 192, 356, 261, 682, 514, 306, 886, 201, 530, 385, 849, 952, 294, 500, 799, 194, 391, 737, 330, 324, 298, 992, 790, 224, 275, 260, 826, 97, 72, 210, 866, 649, 951, 919, 748, 63, 685, 958, 956, 804, 564, 518, 183, 428, 400, 537, 721, 346, 207, 153, 323, 971, 611, 662, 116, 197, 109, 91, 795, 529, 343, 126, 862, 747, 685, 469, 10, 770, 881, 934, 984, 723, 403, 895, 360, 568, 449, 172, 541, 958, 272, 383, 877, 308, 359, 970, 707, 583, 308, 48, 770, 930, 30, 569, 208, 3, 311, 20, 100, 609, 939, 887, 422, 825, 785, 930, 370, 904, 989, 241, 969, 379, 143, 376, 972, 962, 28, 889, 61, 443, 638, 216, 348, 338, 347, 160, 66, 406, 391, 159, 638, 31, 295, 204, 826, 420, 196, 153, 449, 425, 855, 331, 143, 565, 487, 838, 140, 9, 564, 918, 615, 533, 135, 232, 564, 957, 360, 591, 793, 576, 163, 746, 859, 377, 760, 858, 711, 86, 662, 434, 159, 558, 660, 279, 268, 840, 948, 735, 315, 574, 676, 126, 341, 912, 689, 739, 894, 821, 706, 625, 490, 917, 478, 201, 671, 993, 932, 149, 899, 52, 237, 759, 187, 267, 472, 256, 772, 783, 98, 117, 906, 516, 911, 180, 635, 25, 225, 380, 823, 712, 164, 266, 343, 216, 732, 448, 502, 541, 740, 664, 576, 954, 612, 726, 902, 772, 454, 531, 411, 943, 973, 750, 703, 327, 850, 917, 77, 5, 220, 113, 802, 913, 403, 791, 181, 998, 10, 859, 525, 345, 919, 431, 668, 675, 527, 833, 462, 438, 291, 523, 605, 916, 457, 420, 405, 115, 417, 660, 279, 261, 685, 772, 596, 388, 307, 764, 224, 843, 322, 206, 840, 407, 975, 639, 401, 852, 91, 542, 327, 60, 330, 757, 182, 82, 603, 637, 793, 93, 615, 614, 733, 136, 864, 187, 16, 102, 863, 972, 987, 163, 306, 501, 34, 477, 840, 492, 700, 121, 706, 80, 787, 138, 105, 660, 834, 229, 798, 827, 310, 448, 609, 230, 690, 832, 561, 489, 579, 586, 60, 291, 388, 665, 309, 660, 407, 702, 200, 153, 313, 606, 970, 123, 33, 253, 273, 709, 277, 832, 997, 108, 40, 888, 227, 481, 860, 283, 940, 858, 608, 284, 990, 711, 590, 850, 806, 239, 52, 943, 801, 195, 764, 95, 710, 410, 386, 664, 593, 900, 50, 112, 494, 963, 156, 754, 936, 458, 965, 19, 86, 225, 723, 569, 184, 97, 868, 194, 340, 754, 412, 602, 487, 61, 209, 695, 346, 347, 860, 719, 307, 100, 95, 97, 821, 843, 941, 745, 821, 286, 587, 492, 201, 116, 665, 531, 527, 424, 812, 253, 424, 426, 459, 413, 343, 980, 836, 291, 506, 833, 179, 486, 916, 9, 430, 575, 596, 922, 808, 145, 269, 897, 512, 404, 811, 742, 685, 728, 567, 269, 776, 103, 855, 745, 718, 35, 853, 779, 97, 71, 146, 80, 746, 842, 15, 128, 965, 941, 512, 227, 480, 30, 958, 342, 665, 605, 191, 531, 964, 364, 515, 359, 606, 867, 822, 820, 536, 313, 693, 207, 19, 911, 335, 308, 101, 430, 922, 685, 294, 641, 910, 113, 410, 141, 250, 690, 546, 700, 729, 559, 609, 481, 720, 503, 976, 766, 759, 7, 798, 882, 74, 859, 648, 182, 299, 425, 785, 542, 846, 683, 390, 853, 897, 938, 416, 89, 837, 624, 247, 181, 293, 878, 144, 933, 938, 580, 467, 252, 480, 207, 955, 658, 665, 20, 26, 954, 564, 347, 449, 759, 26, 446, 521, 14, 301, 657, 321, 710, 613, 980, 388, 188, 879, 636, 990, 203, 188, 432, 963, 129, 820, 630, 300, 509, 822, 237, 255, 373, 277, 838, 153, 395, 400, 692, 227, 103, 518, 218, 447, 384, 315, 931, 813, 396, 324, 389, 502, 640, 299, 287, 780, 297, 376, 977, 516, 512, 212, 219, 329, 555, 810, 398, 486, 289, 485, 798, 416, 598, 952, 49, 894, 572, 358, 697, 21, 770, 647, 725, 44, 542, 683, 214, 361, 887, 814, 45, 231, 94, 8, 282, 165, 82, 496, 319, 699, 626, 852, 146, 459, 541, 535, 874, 314, 725, 944, 777, 306, 554, 459, 208, 637, 787, 447, 976, 408, 912, 107, 444, 962, 3, 565, 301, 407, 164, 857, 437, 317, 652, 98, 764, 818, 41, 600, 737, 288, 176, 902, 136, 567, 128, 190, 394, 749, 235, 908, 42, 276, 824, 748, 684, 592, 979, 393, 568, 363, 367, 163, 52, 983, 642, 179, 335, 16, 126, 478, 130, 259, 583, 868, 129, 295, 752, 596, 167, 71, 856, 622, 640, 864, 770, 644, 431, 5, 195, 365, 448, 429, 701, 64, 952, 854, 880, 770, 168, 88, 79, 157, 961, 368, 472, 421, 217, 719, 709, 271, 147, 975, 296, 816, 909, 962, 764, 535, 251, 438, 878, 696, 753, 881, 570, 318, 253, 45, 666, 52, 931, 496, 433, 313, 810, 138, 678, 334, 123, 988, 212, 508, 66, 770, 206, 751, 750, 780, 64, 441, 466, 207, 337, 516, 510, 674, 796, 408, 175, 920, 437, 573, 938, 634, 848, 878, 561, 990, 718, 826, 948, 696, 649, 800, 569, 411, 663, 422, 582, 707, 843, 334, 202, 986, 702, 494, 579, 150, 725, 562, 803, 380, 340, 832, 156, 979, 832, 376, 602, 511, 234, 414, 688, 160, 184, 982, 978, 238, 413, 490, 633, 899, 585, 251, 606, 526, 963, 897, 350, 39, 242, 657, 539, 452, 739, 538, 267, 831, 165, 460, 177, 185, 720, 934, 802, 440, 644, 490, 82, 168, 798, 71, 197, 977, 173, 458, 81, 204, 235, 803, 112, 139, 103, 399, 268, 420, 879, 991, 643, 578, 106, 972, 122, 177, 444, 398, 262, 348, 691, 496, 997, 276, 446, 825, 171, 614, 549, 121, 281, 730, 211, 341, 112, 284, 408, 698, 730, 17, 363, 546, 89, 672, 605, 974, 938, 866, 168, 377, 705, 559, 47, 340, 400, 527, 215, 779, 114, 761, 51, 483, 881, 92, 549, 390, 679, 583, 328, 661, 250, 391, 203, 336, 481, 661, 752, 125, 707, 89, 82, 275, 63, 359, 660, 178, 21, 780, 287, 42, 219, 5, 500, 380, 820, 772, 95, 816, 44, 9, 748, 484, 678, 545, 276, 589, 478, 157, 663, 901, 594, 777, 757, 74, 947, 247, 448, 55, 570, 479, 313, 785, 981, 958, 795, 680, 552, 393, 389, 944, 280, 596, 864, 619, 636, 611, 512, 852, 892, 283, 184, 299, 947, 766, 614, 312, 625, 826, 149, 726, 377, 733, 474, 805, 18, 679, 662, 403, 416, 964, 1, 337, 530, 541, 752, 478, 22, 369, 981, 946, 777, 821, 957, 833, 275, 837, 584, 490, 582, 37, 121, 594, 951, 129, 668, 258, 118, 246, 319, 362, 896, 755, 521, 504, 948, 71, 248, 177, 474, 176, 351, 803, 54, 237, 155, 677, 660, 33, 871, 115, 486, 597, 294, 769, 425, 536, 245, 956, 382, 341, 371, 184, 708, 234, 544, 569, 794, 216, 387, 537, 738, 84, 520, 810, 281, 657, 502, 33, 91, 159, 503, 520, 924, 668, 16, 287, 33, 229, 344, 374, 349, 474, 606, 672, 896, 915, 595, 511, 441, 123, 102, 569, 52, 199, 654, 396, 710, 715, 434, 59, 245, 610, 36, 917, 661, 927, 413, 270, 13, 117, 370, 516, 400, 215, 195, 216, 984, 237, 476, 615, 164, 499, 405, 477, 674, 760, 196, 285, 608, 56, 907, 966, 532, 622, 51, 221, 716, 194, 327, 91, 880, 347, 710, 252, 267, 473, 400, 516, 376, 385, 609, 357, 320, 958, 196, 665, 538, 792, 935, 64, 404, 420, 100, 671, 126, 968, 855, 877, 94, 177, 731, 523, 253, 139, 8, 152, 393, 732, 543, 246, 348, 565, 318, 740, 489, 902, 22, 17, 998, 973, 49, 298, 381, 611, 10, 32, 429, 11, 221, 337, 150, 136, 717, 889, 530, 522, 332, 805, 942, 28, 565, 931, 216, 757, 61, 609, 170, 544, 872, 905, 644, 595, 208, 246, 680, 113, 697, 175, 639, 156, 679, 244, 961, 883, 361, 265, 802, 883, 359, 462, 839, 485, 718, 272, 280, 204, 252, 984, 280, 409, 102, 398, 615, 909, 296, 370, 481, 882, 956, 214, 230, 766, 259, 35, 922, 421, 706, 271, 272, 370, 156, 848, 552, 50, 485, 976, 536, 270, 70, 796, 902, 963, 452, 379, 35, 991, 947, 551, 378, 897, 57, 307, 352, 421, 669, 74, 702, 388, 141, 559, 458, 980, 463, 615, 756, 681, 887, 231, 332, 207, 528, 818, 656, 331, 254, 979, 787, 445, 495, 348, 370, 724, 10, 401, 200, 646, 327, 668, 17, 242, 374, 302, 755, 861, 980, 830, 376, 77, 8, 441, 32, 589, 460, 990, 804, 189, 23, 868, 869, 996, 943, 363, 715, 237, 628, 420, 73, 565, 199, 214, 38, 333, 209, 726, 327, 175, 912, 58, 799, 625, 372, 66, 976, 169, 120, 475, 667, 204, 188, 90, 28, 496, 704, 1, 649, 474, 746, 449, 697, 252, 712, 332, 289, 287, 625, 189, 953, 889, 462, 63, 302, 378, 79, 206, 41, 897, 823, 230, 908, 382, 665, 575, 36, 60, 732, 529, 809, 477, 66, 290, 291, 89, 591, 961, 583, 461, 252, 702, 479, 532, 530, 680, 224, 53, 788, 642, 309, 1000, 366, 891, 352, 822, 402, 152, 545, 324, 113, 633, 967, 267, 454, 381, 723, 272, 14, 506, 20, 419, 308, 461, 644, 526, 713, 978, 482, 468, 336, 729, 289, 795, 496, 581, 326, 383, 530, 226, 167, 421, 522, 386, 830, 273, 91, 484, 116, 676, 747, 107, 395, 229, 958, 902, 756, 962, 262, 105, 703, 84, 511, 991, 481, 701, 449, 689, 888, 655, 599, 600, 306, 538, 492, 499, 803, 881, 445, 981, 316, 473, 889, 383, 785, 679, 765, 573, 491, 589, 589, 480, 124, 145, 741, 624, 898, 923, 725, 915, 682, 232, 131, 974, 398, 735, 906, 678, 753, 640, 312, 644, 790, 212, 301, 123, 945, 253, 447, 943, 197, 18, 27, 916, 573, 327, 589, 640, 934, 615, 209, 470, 529, 655, 899, 288, 15, 401, 592, 840, 92, 633, 575, 707, 814, 424, 354, 682, 127, 194, 374, 855, 566, 190, 594, 728, 973, 178, 645, 275, 603, 88, 712, 367, 32, 544, 298, 738, 561, 963, 906, 460, 626, 266, 143, 727, 504, 33, 836, 442, 361, 628, 431, 384, 554, 244, 24, 482, 416, 765, 735, 446, 243, 247, 454, 913, 932, 560, 494, 528, 648, 594, 357, 935, 596, 462, 788, 621, 536, 660, 759, 564, 200, 302, 566, 936, 741, 592, 217, 724, 219, 118, 832, 299, 819, 251, 487, 129, 473, 96, 241, 167, 64, 169, 516, 381, 857, 976, 218, 767, 490, 837, 504, 407, 707, 203, 342, 387, 475, 735, 175, 706, 786, 335, 171, 40, 232, 821, 888, 131, 866, 159, 976, 978, 253, 53, 619, 849, 642, 478, 460, 19, 218, 230, 218, 453, 667, 336, 949, 931, 89, 588, 488, 449, 568, 566, 526]
        C = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
        K = 10

    N = len(w)
    cplex_m_global = Model('multiple knapsack', log_output=False)
    cplex_m_global.data = -1

    # If set to X, information will be displayed every X iterations
    # m.parameters.mip.interval.set(1)

    # Turning off presolving callbacks
    cplex_m_global.parameters.preprocessing.presolve.set(0) # Decides whether CPLEX applies presolve during preprocessing to simplify and reduce problems
    cplex_m_global.parameters.preprocessing.aggregator.set(0) # Invokes the aggregator to use substitution where possible to reduce the number of rows and columns before the problem is solved. If set to a positive value, the aggregator is applied the specified number of times or until no more reductions are possible.
    cplex_m_global.parameters.preprocessing.reduce.set(0) # 
    # cplex_m.parameters.preprocessing.linear.set(0) # Decides whether linear or full reductions occur during preprocessing. If only linear reductions are performed, each variable in the original model can be expressed as a linear form of variables in the presolved model. This condition guarantees, for example, that users can add their own custom cuts to the presolved model.
    cplex_m_global.parameters.preprocessing.relax.set(0) # Decides whether LP presolve is applied to the root relaxation in a mixed integer program (MIP). Sometimes additional reductions can be made beyond any MIP presolve reductions that were already done. By default, CPLEX applies presolve to the initial relaxation in order to hasten time to the initial solution.
    cplex_m_global.parameters.preprocessing.numpass.set(0) # Limits the number of pre-resolution passes that CPLEX makes during pre-processing. When this parameter is set to a positive value, pre-resolution is applied for the specified number of times or until no further reduction is possible.
    
    cplex_m_global.parameters.advance.set(0) # If 1 or 2, this parameter specifies that CPLEX should use advanced starting information when it initiates optimization.
    cplex_m_global.parameters.preprocessing.qcpduals.set(0) # This parameter determines whether CPLEX preprocesses a quadratically constrained program (QCP) so that the user can access dual values for the QCP.
    cplex_m_global.parameters.preprocessing.qpmakepsd.set(0) # Decides whether CPLEX will attempt to reformulate a MIQP or MIQCP model that contains only binary variables. When this feature is active, adjustments will be made to the elements of a quadratic matrix that is not nominally positive semi-definite (PSD, as required by CPLEX for all QP and most QCP formulations), to make it PSD, and CPLEX will also attempt to tighten an already PSD matrix for better numerical behavior.
    cplex_m_global.parameters.preprocessing.qtolin.set(0) # This parameter switches on or off linearization of the quadratic terms in the objective function of a quadratic program (QP) or of a mixed integer quadratic program (MIQP) during preprocessing.
    cplex_m_global.parameters.preprocessing.repeatpresolve.set(0) # Specifies whether to re-apply presolve, with or without cuts, to a MIP model after processing at the root is otherwise complete.
    cplex_m_global.parameters.preprocessing.dual.set(0) # Decides whether the CPLEX pre-solution should pass the primal or dual linear programming problem to the linear programming optimization algorithm.
    cplex_m_global.parameters.preprocessing.fill.set(0) # Limits number of variable substitutions by the aggregator. If the net result of a single substitution is more nonzeros than this value, the substitution is not made.
    cplex_m_global.parameters.preprocessing.coeffreduce.set(0) # Decides how coefficient reduction is used. Coefficient reduction improves the objective value of the initial (and subsequent) LP relaxations solved during branch and cut by reducing the number of non-integral vertices. By default, CPLEX applies coefficient reductions during preprocessing of a model.
    cplex_m_global.parameters.preprocessing.boundstrength.set(0) # Decides whether to apply bound strengthening in mixed integer programs (MIPs). Bound strengthening tightens the bounds on variables, perhaps to the point where the variable can be fixed and thus removed from consideration during branch and cut.
    cplex_m_global.parameters.preprocessing.dependency.set(0) # Decides whether to activate the dependency checker. If on, the dependency checker searches for dependent rows during preprocessing. If off, dependent rows are not identified.
    cplex_m_global.parameters.preprocessing.folding.set(0) # Decides whether folding will be automatically executed, during the preprocessing phase, in a LP model.
    cplex_m_global.parameters.preprocessing.symmetry.set(0) # Decides whether symmetry breaking reductions will be automatically executed, during the preprocessing phase, in either a MIP or LP model.
    cplex_m_global.parameters.preprocessing.sos1reform.set(-1) # This parameter allows you to control the reformulation of special ordered sets of type 1 (SOS1), which can be applied during the solution process of problems containing these sets.
    cplex_m_global.parameters.preprocessing.sos2reform.set(-1) # This parameter allows you to control the reformulation of special ordered sets of type 2 (SOS2), which can be applied during the solution process of problems containing these sets.
    cplex_m_global.parameters.mip.cuts.mircut(-1) # Decides whether or not to generate MIR cuts (mixed integer rounding cuts) for the problem.

    # Registering the branching callback
    nodes_to_process = []
    branch_instance = cplex_m_global.register_callback(BranchCB)
    branch_instance.init(nodes_to_process)

    node_instance = cplex_m_global.register_callback(NodeCB)
    node_instance.init(nodes_to_process)

    mipinfo_instance = cplex_m_global.register_callback(MIPInfoCB)
    mipinfo_instance.init(nodes_to_process)


    # Adding variables
    x = cplex_m_global.integer_var_matrix(N, K, name="x")

    # Adding constraints
    for j in range(K):
        cplex_m_global.add_constraint(sum(w[i]*x[i, j] for i in range(N)) <= C[j])
    for i in range(N):
        cplex_m_global.add_constraint(sum(x[i, j] for j in range(K)) <= 10)

    # Setting up the objective function
    obj_fn = sum(v[i]*x[i,j] for i in range(N) for j in range(K))
    cplex_m_global.set_objective("max", obj_fn)

    # Displaying info
    # m.print_information()

    # Solving... Should take a while
    sol = cplex_m_global.solve()

    # Printing solution
    # m.print_solution()

    # Displaying final information
    # cplex_m.print_information()     

    print(f"branch_instance.times_called: {branch_instance.times_called}")
    print(f"node_instance.times_called: {node_instance.times_called}")
    print(f"mipinfo_instance.times_called: {mipinfo_instance.times_called}")
    # print(f"max: {m.solution.get_objective_value()}")

    if sol is None:
        print("Infeasible")

    # print("==> Done.")
    client_socket.sendall(b"done")

class CustomEnv(Env):
    def __init__(self):
        # init the model
        # threading.Thread(target=init_cplex_model, args=()).start()
        # data = server_conn.recv(1024).decode()
        # Actions we can take (random branch, most fractional)
        self.action_space = Discrete(7)
        # array of possible state values
        self.observation_space = Box(low=np.array([0]), high=np.array([10**5]), dtype=np.float32)
        # Set start state
        # state, _ = np.array([np.array([i], dtype=np.float64) for i in data.split(sep=';')], dtype=np.float64)
        self.state = -1
        self.done = False
        self.data = -1
    def step(self, action):
        
        self.data = action
        server_conn.send(str(action).encode())
        data = server_conn.recv(1024).decode()

        if data == "done":
            self.done = True
            reward = 0
            info = {}
            return self.state, reward, self.done, self.state, info
        
        self.last_state = self.state
        #self.state = np.array([np.array([i], dtype=np.float32) for i in data.split(sep=';')], dtype=np.float32)
        self.state =  cplex_m_global.data # np.array([np.float32(data)])

        # Calculate reward
        if self.state[0] < self.last_state[0]:
            reward = 100
        if self.state[0] > self.last_state[0]:
            reward = 0
        else: 
            reward = 1

        # Set placeholder for info
        info = {}
        
        # Return step information
        return self.state, reward, self.done, self.state, info

    def reset(self):

        # reinit the model
        threading.Thread(target=init_cplex_model, args=()).start()

        # Reset state
        data = server_conn.recv(1024).decode()
        if data == "done":
            self.done = True
            return self.state
        
        self.state = cplex_m_global.data # np.array([np.float32(data)])
        self.done = False
        return self.state

class DQN:
    def __init__(self, env):
        self.env = env
        self.memory = deque(maxlen=5*10**5)
        self.batch_size = 32
        self.gamma = 0.99
        self.exploration_max = 1.0
        self.exploration_min = 0.01
        self.exploration_decay = 0.99999

        self.learning_rate = 0.001
        self.tau = .125

        self.model = self.create_model()
        self.target_model = self.create_model()
        self.loss_history = []
        self.fit_count = 0

    def create_model(self):
        _model = Sequential()
        state_shape = self.env.observation_space.shape
        _model.add(Dense(24, input_dim=state_shape[0], activation="relu"))
        _model.add(Dense(48, activation="relu"))
        _model.add(Dense(24, activation="relu"))
        _model.add(Dense(self.env.action_space.n))
        _model.compile(loss="mse",
                      optimizer=Adam(learning_rate=self.learning_rate))

        return _model

    def get_action(self, state):
        self.exploration_max *= self.exploration_decay
        self.exploration_max = max(self.exploration_min, self.exploration_max)
        if np.random.random() < self.exploration_max:
            return self.env.action_space.sample()
        # q_values = self.model.predict(state, verbose=0)
        q_values = self.model(state).numpy()
        return np.argmax(q_values[0])

    def remember(self, state, action, reward, new_state, done):
        self.memory.append([state, action, reward, new_state, done])

    def replay(self):
        if len(self.memory) < self.batch_size:
            return 
        samples = random.sample(self.memory, self.batch_size)
        states = np.array([sample[0] for sample in samples])
        states, actions, rewards, new_states, dones = zip(*samples)
        #targets = self.target_model.predict(states, batch_size=self.batch_size, verbose=False).numpy()[0]
        # Qs_future = [max(self.target_model(i).numpy()[0]) for i in new_states]
        targets = []
        for state, action, reward, new_state, done in samples:
            # target = self.target_model.predict(state, verbose=0)
            target = self.target_model(state).numpy()[0]
            if done:
                target[action] = reward
            else:
                # Q_future = max(self.target_model.predict(new_state, verbose=0)[0])
                Q_future = max(self.target_model(new_state)[0])
                target[action] = reward + Q_future * self.gamma
            # targets.append(target[0])
            targets.append(target)
            # self.model.fit(state, target, epochs=1, verbose=0)
        # targets = np.delete(targets, 0, axis=0)
        states = np.array(states)
        targets = np.array(targets)
        self.loss_history.append(self.model.fit(states, targets, verbose=0).history['loss'][0])
        self.fit_count += 1
        if self.fit_count % 500 == 0 and self.fit_count > 0:
            plt.plot(self.loss_history)
            plt.savefig("loss"+str(self.fit_count)+".png")
            plt.close()
            pd.DataFrame(self.loss_history).to_csv("loss.csv")
            

    def target_train(self):
        weights = self.model.get_weights()
        target_weights = self.target_model.get_weights()
        for i in range(len(target_weights)):
            target_weights[i] = weights[i] * self.tau + target_weights[i] * (1 - self.tau)
        self.target_model.set_weights(target_weights)

    def save_model(self, fn):
        self.model.save(fn)

def create_client_socket(client_socket):
    client_socket.connect((host, port))  # connect to the server
    client_socket.sendall(f'{bcolors.OKBLUE} Client (CPLEX) connected successfully! {bcolors.ENDC}'.encode())

if __name__ == "__main__":
    
    # get the hostname
    host = socket.gethostname()
    port = 5000  # initiate port no above 1024

    server_socket = socket.socket()  # get instance
    # look closely. The bind() function takes tuple as argument
    server_socket.bind((host, port))  # bind host address and port together

    # configure how many client the server can listen simultaneously
    server_socket.listen(5)
    
    
    client_socket = socket.socket()  # instantiate

    threading.Thread(target=create_client_socket, args=(client_socket,)).start()

    server_conn, address = server_socket.accept()  # accept new connection
    print("Message: "+server_conn.recv(1024).decode("ASCII"))

    EnvCustom = CustomEnv()
    DQN_model = DQN(env=EnvCustom) # DQN agent

    cplex_m_global = Model('multiple knapsack', log_output=False)

    epochs = 500
    episodes = 5000
    best_reward = -9999
    best_model = None

    history = []
    print_times = False

    for epoch in range(epochs):
        cur_state = EnvCustom.reset()
        done = EnvCustom.done
        rewards_history = []
        start = time.time()
        i=0
        while not done and i < episodes:
            i+=1
            if print_times:
                s = time.time()
            action = DQN_model.get_action(cur_state)
            if print_times:
                e = time.time()
                print(f"Time taken to get action: {e-s}")
                s = time.time()
            new_state, reward, done, new_curr_state, _ = EnvCustom.step(action)
            if print_times:
                e = time.time()
                print(f"Time taken to do step: {e-s}")
                s = time.time()
            DQN_model.remember(cur_state, action, reward, new_state, done)
            if print_times:
                e = time.time()
                print(f"Time taken to remember: {e-s}")
                s = time.time()
            DQN_model.replay()  # internally iterates default (prediction) model
            if print_times:
                e = time.time()
                print(f"Time taken to replay: {e-s}")
                s = time.time()
            DQN_model.target_train()  # iterates target model
            if print_times:
                e = time.time()
                print(f"Time taken to target_train: {e-s}")
            cur_state = new_curr_state
            rewards_history.append(reward)

        end = time.time()
        avg_reward = np.mean(rewards_history)
        deviation = np.std(rewards_history)
        history.append((epoch, avg_reward, deviation, len(rewards_history)))
        pd.DataFrame(history).to_csv("avg_reward_history.csv")
        print(f"Time elapsed - episode {epoch}: {'{:.3f}'.format(end - start)} seconds and {i} iterations. Avg Reward: {avg_reward}, Deviation: {deviation}")

        if avg_reward > best_reward:
            print(f"> Saving new best model...")
            best_reward = avg_reward
            best_model = DQN_model
            DQN_model.save_model(r"C:\\Users\\arthu\Documents\\0.Msc. Eng. Sist. Comp\\otimia-g1-main\\DQN-batchTrain\\")

    EnvCustom.close()

    


