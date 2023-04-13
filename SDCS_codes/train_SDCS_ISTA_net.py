from torch import nn
from dataset import dataset
import torch
from torch.autograd import Variable
import os
import numpy as np
import glob
from utils import *
from scipy import io


def compute_loss(outputs, target):
    loss = []
    for output in outputs:
        loss.append(torch.mean((output - target) ** 2))
    return loss


def get_final_loss(loss_all):
    output = 0
    for loss in loss_all:
        output += loss
    return output


def get_loss(outputs, costs, target,):
    loss1 = torch.mean((outputs[-1] - target) ** 2)
    loss2 = 0
    num = 0
    for n in range(len(costs)):
        num += 1

        cost = costs[n]
        loss2 += torch.mean(cost**2)
    loss2 /= num

    return loss1, loss2

def get_mask(data_batch,test=0):
    data = torch.zeros([data_batch,math.ceil(1089*0.5),1089])
    for n in range(data_batch):
        if test==0:
            random_num = math.ceil(1089*(random.randint(1,50)/100))
        else:
            random_num = math.ceil(1089*(test/100))
        data[n,0:random_num,:] = 1
    return data

def train(model, opt, train_loader, epoch, batch_size, PhaseNum, H,CS_ratio):
    model.train()
    n = 0
    for data, target in train_loader:
        n = n + 1
        opt.zero_grad()  # 清空梯度
        data, target = torch.transpose(torch.reshape(data, [-1, 33 * 33]), 0, 1), torch.transpose(
            torch.reshape(target, [-1, 33 * 33]), 0, 1)
        data, target = Variable(data.float().cuda()), Variable(target.float().cuda())

        data_batch = data.shape[1]
        sampling_matrix_mask = get_mask(data_batch)
        sampling_matrix_mask = Variable(sampling_matrix_mask.float().cuda())

        outputs, costs= model(data, sampling_matrix_mask, PhaseNum)

        # loss_all = compute_loss(outputs,data)
        # loss = get_final_loss(loss_all)
        # loss = torch.mean((outputs[-1]-target)**2)
        loss1, loss2 = get_loss(outputs, costs, target)
        loss = loss1 + 0.01*loss2
        loss.backward()
        opt.step()
        if n % 25 == 0:
            output = "CS_ratio: %d PhaseNum: %d [%02d/%02d] loss1: %.4f loss2: %.4f" % (
            CS_ratio, PhaseNum, epoch, batch_size * n, loss1.data.item(),loss2.data.item())
            # output = "[%02d/%02d] cost: %.4f, cost_sym: %.4f \n" % (epoch, batch_size*n,
            #                                        cost.data.item(),cost_sym.data.item())
            print(output)


def get_val_result(model, num, is_cuda=True):
    model.eval()
    val_CS_ratios = [50, 40, 30, 25, 10, 4, 1]
    test_set_path = "../../dataset/BSR_bsds500/BSR/BSDS500/data/images/val"
    test_set_path = glob.glob(test_set_path + '/*.tif')
    ImgNum = len(test_set_path)  # 测试图像的数量
    PSNR_All = np.zeros([1, ImgNum], dtype=np.float32)
    PSNR_CS_ratios = np.zeros([1, len(val_CS_ratios)], dtype=np.float32)
    model.eval()
    n=0
    with torch.no_grad():
        for CS_ratio in val_CS_ratios:
            for img_no in range(ImgNum):
                imgName = test_set_path[img_no]  # 当前图像的名字

                [Iorg, row, col, Ipad, row_new, col_new] = imread_CS_py(imgName)
                Icol = img2col_py(Ipad, 33) / 255.0  # 返回 行向量化后的图像数据
                # Img_input = np.dot(Icol, Phi_input)  # 压缩感知降采样
                # Img_output = Icol
                if is_cuda:
                    inputs = Variable(torch.from_numpy(Icol.astype('float32')).cuda())
                else:
                    inputs = Variable(torch.from_numpy(Icol.astype('float32')))
                # if model.network == "ista_plus" or model.network == "ista":
                #     output, _ = model(inputs)
                # else:
                #     output = model(inputs)
                sampling_matrix_mask = get_mask(inputs.shape[1], CS_ratio)
                sampling_matrix_mask = Variable(sampling_matrix_mask.float().cuda())
                outputs, costs = model(inputs, sampling_matrix_mask, num)
                output = outputs[-1]
                if is_cuda:
                    output = output.cpu().data.numpy()
                else:
                    output = output.data.numpy()
                images_recovered = col2im_CS_py(output, row, col, row_new, col_new)
                rec_PSNR = psnr(images_recovered * 255, Iorg)  # 计算PSNR的值
                PSNR_All[0, img_no] = rec_PSNR
            PSNR_CS_ratios[0, n] = np.mean(PSNR_All)
            n+=1

    return PSNR_CS_ratios


def load_sampling_matrix(CS_ratio):
    path = "../../dataset/sampling_matrix"
    data = io.loadmat(os.path.join(path, str(CS_ratio) + '.mat'))['sampling_matrix']
    return data


def get_Q(data_set,A):
    A = torch.from_numpy(A)
    n = 0
    data_loader = torch.utils.data.DataLoader(data_set, batch_size=len(data_set),
                                shuffle=True, num_workers=2)
    for data, target in data_loader:
        data = torch.transpose(torch.reshape(data, [-1, 33 * 33]), 0, 1)
        target = torch.transpose(torch.reshape(target, [-1, 33 * 33]), 0, 1)
        y = torch.matmul(A.float(),data.float())
        x = target.float()
        if n==0:
            ys = y
            Xs = x
            n = 1
        else:
            ys = torch.cat([ys,y],dim=1)
            Xs = torch.cat([Xs,x],dim=1)
    Q = torch.matmul(torch.matmul(Xs,torch.transpose(ys,0,1)),
                     torch.inverse(torch.matmul(ys, torch.transpose(ys, 0, 1))))
    return Q.numpy()



class ISTA_net_f(nn.Module):  # 模型
    # 用于构造 ISTA net 神经网络的那一部分
    def __init__(self,network_name):
        super().__init__()
        """
        构建最核心的网络
        """

        conv_size = 32
        filter_size = 3
        self.network = network_name
        self.register_parameter("soft_thr", nn.Parameter(torch.tensor(0.1)))  # 自己注册一个变量

        self.conv1 = nn.Conv2d(1,conv_size,filter_size,bias=False,padding=1)
        self.conv2 = nn.Conv2d(conv_size,conv_size,filter_size,bias=False,padding=1)
        self.conv3 = nn.Conv2d(conv_size, conv_size, filter_size, bias=False,padding=1)
        self.conv4 = nn.Conv2d(conv_size, conv_size, filter_size, bias=False,padding=1)
        self.conv5 = nn.Conv2d(conv_size, conv_size, filter_size, bias=False,padding=1)
        self.conv6 = nn.Conv2d(conv_size, 1, filter_size, bias=False,padding=1)

    def forward(self,input):
        input = torch.unsqueeze(torch.reshape(torch.transpose(input, 0, 1), [-1, 33, 33]), dim=1)
        if self.network == "ISTA_net":
            x = torch.relu(self.conv2(self.conv1(input)))
            x = self.conv3(x)

            x = torch.mul(torch.sign(x),torch.relu(torch.abs(x)-self.soft_thr))
            x = torch.relu(self.conv4(x))
            x = self.conv6(self.conv5(x))  # 前向传播

            cost = torch.relu(self.conv2(self.conv1(input)))
            cost = self.conv3(cost)
            cost = torch.relu(self.conv4(cost))
            cost = self.conv6(self.conv5(cost))
            cost = cost - input
            output = x
            output = torch.transpose(torch.reshape(output, [-1, 33 * 33]), 0, 1)
            cost = torch.transpose(torch.reshape(cost, [-1, 33 * 33]), 0, 1)

        elif self.network == "ISTA_net_plus":
            x1 = self.conv1(input)
            x1_1 = torch.relu(self.conv2(x1))
            x2 = self.conv3(x1_1)
            x3 = torch.mul(torch.sign(x2), torch.relu(torch.abs(x2) - self.soft_thr))
            x4 = torch.relu(self.conv4(x3))
            x5 = self.conv6(self.conv5(x4))  # 前向传播

            cost = torch.relu(self.conv2(x1))
            cost = self.conv3(cost)
            cost = torch.relu(self.conv4(cost))
            cost = self.conv5(cost)
            cost = cost - x1
            output = x5 + input
            output = torch.transpose(torch.reshape(output, [-1, 33 * 33]), 0, 1)
            cost = torch.transpose(torch.reshape(cost, [-1, 33 * 33]), 0, 1)

        return output, cost


class ISTA_net(nn.Module):
    def __init__(self,layer_num, A,network_name="ISTA_net_plus"):
        super().__init__()
        self.layer_num = layer_num
        self.network_name = network_name
        self.fs = []
        self.steps = []
        self.register_parameter("A",nn.Parameter(torch.from_numpy(A).float(),requires_grad=True))
        self.register_parameter("Q", nn.Parameter(torch.from_numpy(np.transpose(A)).float(), requires_grad=True))

        for n in range(layer_num):
            self.fs.append(ISTA_net_f(network_name))
            self.register_parameter("step_"+str(n+1), nn.Parameter(torch.tensor(0.1)))
            self.steps.append(eval("self.step_"+str(n+1)))

        for n,f in enumerate(self.fs):
            self.add_module("f_"+str(n+1),f)

    def forward(self, inputs, sampling_matrix_mask, output_layers):
        """
        此处就是前向传播，返回每一层的输出
        :param inputs: 此处的inputs 为图像数据
        :return:
        """
        outputs = []
        costs = []
        now_mask = self.A * sampling_matrix_mask
        now_Q = torch.transpose(sampling_matrix_mask, 1, 2) * self.Q
        y = self.sampling(now_mask, inputs)
        X = torch.matmul(now_Q, y)

        for n in range(output_layers):
            step = self.steps[n]
            f = self.fs[n]

            temp = self.block1(now_mask,X,y,step)
            temp = torch.squeeze(temp)
            temp = torch.transpose(temp,0,1)
            X,cost = f(temp)

            outputs.append(X.clone())
            costs.append(cost.clone())
            temp = torch.transpose(temp, 0, 1)
            X = torch.unsqueeze(temp,dim=2)
        return outputs,costs


    def sampling(self,A, inputs):
        # inputs = torch.squeeze(inputs)
        # inputs = torch.reshape(inputs,[-1,33*33])  # 矩阵向量hua
        inputs = torch.transpose(inputs,0,1)
        inputs = torch.unsqueeze(inputs,dim=2)
        outputs = torch.matmul(A, inputs)
        return outputs

    def block1(self,A,X,y,step):
        # X = torch.squeeze(X)
        # X = torch.transpose(torch.reshape(X, [-1, 33 * 33]),0,1)  # 矩阵向量hua
        outputs = step*torch.matmul(torch.transpose(A,1,2),y-torch.matmul(A,X))
        outputs = outputs + X
        # outputs = torch.unsqueeze(torch.reshape(torch.transpose(outputs,0,1),[-1,33,33]),dim=1)
        return outputs


if __name__ == "__main__":
    is_cuda = True
    CS_ratio = 25  # 4, 10, 25, 30, 40, 50
    CS_ratios = [50]
    # n_output = 1089
    # PhaseNumbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]  # block 数目为 5
    PhaseNumbers = [9]
    learning_rate = 0.0001
    EpochNum = 100
    batch_size = 64
    results_saving_path = "../../results_c4"
    net_name = "ISTA_Net"

    if not os.path.exists(results_saving_path):
        os.mkdir(results_saving_path)

    if not os.path.exists(results_saving_path):
        os.mkdir(results_saving_path)

    results_saving_path = os.path.join(results_saving_path, net_name)
    if not os.path.exists(results_saving_path):
        os.mkdir(results_saving_path)

    print('Load Data...')  # jiazaishuju

    train_dataset = dataset(root="../../dataset",train=True, transform=None,
                            target_transform=None)
    print(len(train_dataset))
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size,
                                               shuffle=True, num_workers=2)

    for PhaseNumber in PhaseNumbers:
        for CS_ratio in CS_ratios:
            A = load_sampling_matrix(CS_ratio)
            # Q = get_Q(train_dataset, A)
            H = torch.from_numpy(np.matmul(np.transpose(A), A) - np.eye(33 * 33)).float().cuda()
            model = ISTA_net(PhaseNumber, A)
            opt = torch.optim.Adam(model.parameters(), lr=learning_rate)
            model.cuda()

            sub_path = os.path.join(results_saving_path, str(CS_ratio))

            if not os.path.exists(sub_path):
                os.mkdir(sub_path)

            sub_path = os.path.join(sub_path, str(PhaseNumber))

            if not os.path.exists(sub_path):
                os.mkdir(sub_path)

            best_psnr = 0
            for epoch in range(1, EpochNum + 1):
                if epoch == 101:
                    opt.defaults['lr'] *= 0.2
                # psnr_cs_ratios = get_val_result(model, PhaseNumber)
                train(model, opt, train_loader, epoch, batch_size, PhaseNumber, H,CS_ratio)
                psnr_cs_ratios = get_val_result(model, PhaseNumber)
                mean_psnr = np.mean(psnr_cs_ratios)

                print_str = "Phase: %d epoch: %d  psnr: mean %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f" % (
                PhaseNumber, epoch, mean_psnr, psnr_cs_ratios[0, 0], psnr_cs_ratios[0, 1], psnr_cs_ratios[0, 2],
                psnr_cs_ratios[0, 3], psnr_cs_ratios[0, 4], psnr_cs_ratios[0, 5], psnr_cs_ratios[0, 6])
                print(print_str)

                output_file = open(sub_path + "/log_PSNR.txt", 'a')
                output_file.write("PSNR: %.4f\n" % (mean_psnr))
                output_file.close()

                if mean_psnr > best_psnr:
                    best_psnr = mean_psnr
                    output_file = open(sub_path + "/log_PSNR_best.txt", 'a')
                    output_file.write("PSNR: %.4f\n" % (best_psnr))
                    output_file.close()
                    torch.save(model.state_dict(), sub_path + "/best_model.pkl")
