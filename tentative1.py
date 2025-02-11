import argparse
import os
from preprocessing import full_prep
from config_submit import config as config_submit

import torch
from torch.nn import DataParallel
from torch.backends import cudnn
from torch.utils.data import DataLoader
from torch import optim
from torch.autograd import Variable

from layers import acc
from data_detector import DataBowl3Detector,collate
from data_classifier import DataBowl3Classifier
from training.classifier.trainval_classifier import *

from utils import *
from split_combine import SplitComb
from test_detect import test_detect
from importlib import import_module
import pandas

parser = argparse.ArgumentParser(description='PyTorch DataBowl3 Detector')
parser.add_argument('--model1', '-m1', metavar='MODEL', default='net_detector',
                    help='model')
parser.add_argument('--model2', '-m2', metavar='MODEL', default='net_classifier',
                    help='model')
parser.add_argument('-j', '--workers', default=32, type=int, metavar='N',
                    help='number of data loading workers (default: 32)')
parser.add_argument('--epochs', default=None, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=16, type=int,
                    metavar='N', help='mini-batch size (default: 16)')
parser.add_argument('-b2', '--batch-size2', default=3, type=int,
                    metavar='N', help='mini-batch size (default: 16)')
parser.add_argument('--lr', '--learning-rate', default=0.01, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--save-freq', default='5', type=int, metavar='S',
                    help='save frequency')
parser.add_argument('--resume', default='./model/classifier.ckpt', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument('--save-dir', default='', type=str, metavar='SAVE',
                    help='directory to save checkpoint (default: none)')
parser.add_argument('--test1', default=0, type=int, metavar='TEST',
                    help='do detection test')
parser.add_argument('--test2', default=0, type=int, metavar='TEST',
                    help='do classifier test')
parser.add_argument('--test3', default=0, type=int, metavar='TEST',
                    help='do classifier test')
parser.add_argument('--split', default=8, type=int, metavar='SPLIT',
                    help='In the test phase, split the image to 8 parts')
parser.add_argument('--gpu', default='all', type=str, metavar='N',
                    help='use gpu')
parser.add_argument('--n_test', default=8, type=int, metavar='N',
                    help='number of gpu for test')
parser.add_argument('--debug', default=0, type=int, metavar='TEST',
                    help='debug mode')
parser.add_argument('--freeze_batchnorm', default=0, type=int, metavar='TEST',
help='freeze the batchnorm when training')

args = parser.parse_args()


datapath = config_submit['datapath']
valpath = config_submit['valpath']
prep_result_path = config_submit['preprocess_result_path']
skip_prep = config_submit['skip_preprocessing']
skip_detect = config_submit['skip_detect']

nodmodel = import_module(args.model1)
config1, nod_net, loss, get_pbb = nodmodel.get_model()
checkpoint = torch.load(config_submit['detector_param'])
nod_net.load_state_dict(checkpoint['state_dict'])

torch.cuda.set_device(0)
nod_net = nod_net.cuda()
cudnn.benchmark = True
nod_net = DataParallel(nod_net)

if not skip_prep:
    testsplit = full_prep(datapath,prep_result_path,
                          n_worker = config_submit['n_worker_preprocessing'],
                          use_existing=config_submit['use_exsiting_preprocessing'])
    print('Phase decisive')
    valsplit = full_prep(valpath,prep_result_path,
                          n_worker = config_submit['n_worker_preprocessing'],
                          use_existing=config_submit['use_exsiting_preprocessing'])
else:
    testsplit = os.listdir(datapath)
    valsplit = os.listdir(valpath)

#det_res = net.forward(prep_result_path)
#np.save('det_res.npy', det_res)

bbox_result_path = './bbox_result'
if not os.path.exists(bbox_result_path):
    os.mkdir(bbox_result_path)

if not skip_detect:
    margin = 32
    sidelen = 144
    config1['datadir'] = prep_result_path
    split_comber = SplitComb(sidelen,config1['max_stride'],config1['stride'],margin,pad_value= config1['pad_value'])

    dataset = DataBowl3Detector(testsplit,config1,phase='test',split_comber=split_comber)
    test_loader = DataLoader(dataset,batch_size = 1,
        shuffle = False,num_workers = 32,pin_memory=False,collate_fn =collate)

    test_detect(test_loader, nod_net, get_pbb, bbox_result_path,config1,n_gpu=config_submit['n_gpu'])
    
    dataset = DataBowl3Detector(valsplit,config1,phase='test',split_comber=split_comber)
    test_loader = DataLoader(dataset,batch_size = 1,
        shuffle = False,num_workers = 32,pin_memory=False,collate_fn =collate)

    test_detect(test_loader, nod_net, get_pbb, bbox_result_path,config1,n_gpu=config_submit['n_gpu'])


casemodel = import_module(args.model2)
config2 = casemodel.config
args.lr_stage2 = config2['lr_stage']
args.lr_preset2 = config2['lr']
topk = config2['topk']
case_net = casemodel.CaseNet(topk=topk)
args.miss_ratio = config2['miss_ratio']
args.miss_thresh = config2['miss_thresh']
config2['bboxpath'] = bbox_result_path


save_dir = args.save_dir
start_epoch = args.start_epoch
if args.resume:
    checkpoint = torch.load(args.resume)
    if start_epoch == 0:
        start_epoch = checkpoint['epoch'] + 1
    if not save_dir:
        save_dir = checkpoint['save_dir']
    else:
        save_dir = os.path.join('results',save_dir)
case_net.load_state_dict(checkpoint['state_dict'])
if args.epochs == None:
    end_epoch = args.lr_stage2[-1]
else:
    end_epoch = args.epochs
    

case_net = case_net.cuda()
loss = loss.cuda()
case_net = DataParallel(case_net)


save_dir = os.path.join('./', save_dir)
print(save_dir)
print(args.save_freq)
print(testsplit)
# Les noms des dossiers avec les images 3D
trainsplit = testsplit
valsplit = valsplit
testsplit = os.listdir(config_submit['testpath'])

dataset = DataBowl3Classifier(trainsplit,config2,phase = 'train')
train_loader_case = DataLoader(dataset,batch_size = args.batch_size2,
    shuffle = True,num_workers = args.workers,pin_memory=True)

dataset = DataBowl3Classifier(valsplit,config2,phase = 'val')
val_loader_case = DataLoader(dataset,batch_size = max([args.batch_size2,1]),
    shuffle = False,num_workers = args.workers,pin_memory=True)

dataset = DataBowl3Classifier(trainsplit,config2,phase = 'val')
all_loader_case = DataLoader(dataset,batch_size = max([args.batch_size2,1]),
    shuffle = False,num_workers = args.workers,pin_memory=True)

optimizer2 = torch.optim.SGD(case_net.parameters(),
    args.lr,momentum = 0.9,weight_decay = args.weight_decay)

# L'epoque finale est changee manuellement dans net_classifier.py config['lr_stage'][3]; 160 par defaut 
print('Le debut et la fin', start_epoch, end_epoch)

for epoch in range(start_epoch, end_epoch + 1):
    if epoch ==start_epoch:
        lr = args.lr
        debug = args.debug
        args.lr = 0.0
        args.debug = True
        train_casenet(epoch,case_net,train_loader_case,optimizer2,args)
        args.lr = lr
        args.debug = debug
    #
    if epoch>config2['startepoch']:
        train_casenet(epoch,case_net,train_loader_case,optimizer2,args)
        val_casenet(epoch,case_net,val_loader_case,args)
        val_casenet(epoch,case_net,all_loader_case,args)

    if epoch % args.save_freq == 0:            
        state_dict = case_net.module.state_dict()
        for key in state_dict.keys():
            state_dict[key] = state_dict[key].cpu()
        
        if not os.path.exists(save_dir):
            os.mkdir(save_dir)
        open(os.path.join(save_dir, '%03d.ckpt' % epoch), 'w').close()
        torch.save({
            'epoch': epoch,
            'save_dir': save_dir,
            'state_dict': state_dict,
            'args': args},
            os.path.join(save_dir, '%03d.ckpt' % epoch))
