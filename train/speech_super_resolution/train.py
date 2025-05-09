import yamlargparse, os, random
import numpy as np
import argparse
import itertools
import torch
import signal, gc

from dataloader.dataloader import get_dataloader
from solver import Solver
from utils.misc import load_config_json, combine_config_and_args

import sys
sys.path.append('../../')

import warnings
warnings.filterwarnings("ignore")

import torch.multiprocessing as mp
mp.set_start_method('spawn', force=True)

def cleanup(signum, frame):
    print(f"Received signal {signum}, cleaning up...")
    torch.cuda.empty_cache()  # Release CUDA memory
    gc.collect()  # Clean up any orphaned memory
    sys.exit(0)

def main(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    os.environ['PYTORCH_SEED'] = str(args.seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.manual_seed(args.seed)
    device = torch.device('cuda') if args.use_cuda else torch.device('cpu')
    args.device = device

    if args.distributed:
        torch.cuda.set_device(args.local_rank)
        torch.distributed.init_process_group(backend='nccl', rank=args.local_rank, init_method='env://', world_size=args.world_size)

    from networks import network_wrapper
    models = network_wrapper(args).models
    for model in models: 
        model = model.to(device)

    discs = network_wrapper(args).discs
    for disc in discs:
        disc = disc.to(device)

    #if args.network=='MossFormerGAN_SE_16K':
    #    discriminator = network_wrapper(args).discriminator
    #    discriminator = discriminator.to(device)
    #else:
    #    discriminator = None

    if (args.distributed and args.local_rank ==0) or args.distributed == False:
        print("started on " + args.checkpoint_dir + '\n')
        #print(model)
        #print("\nTotal number of model parameters: {} \n".format(sum(p.numel() for p in model.parameters())))
        count = 0
        for model in models:
            print("\nTotal number of model_{} parameters: {} \n".format(count, sum(p.numel() for p in model.parameters() if p.requires_grad)))
            count = count + 1

        if discs is not None:
            count = 0
            for disc in discs:
                print("\nTotal number of discriminator_{} parameters: {} \n".format(count, sum(p.numel() for p in disc.parameters() if p.requires_grad)))
                count = count + 1
        
    '''
    if args.network=='FRCRN_SE_16K':
        params = model.get_params(args.weight_decay)
        optimizer = torch.optim.Adam(params, lr=args.init_learning_rate)
        optimizer_disc = None
    elif args.network=='MossFormer2_SE_48K':
        optimizer = torch.optim.Adam(model.parameters(), lr=args.init_learning_rate)
        optimizer_disc = None
    elif args.network=='MossFormerGAN_SE_16K':
        
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.init_learning_rate)
        optimizer_disc = torch.optim.AdamW(discriminator.parameters(), lr=args.init_learning_rate*2)
    '''
    if args.network=='MossFormer2_SR_48K':
        optimizer_generator = torch.optim.AdamW(models[1].parameters(), args.learning_rate, betas=[args.adam_b1, args.adam_b2])
        optimizer_mossformer = torch.optim.AdamW(models[0].parameters(), args.learning_rate_mossformer, betas=[args.adam_b1, args.adam_b2])
        optimizer_discs = torch.optim.AdamW(itertools.chain(discs[0].parameters(), discs[1].parameters(), discs[2].parameters()),
                                args.learning_rate, betas=[args.adam_b1, args.adam_b2])
    else:
        print(f'in Main, {args.network} is not implemented!')
        return

    train_sampler, train_generator = get_dataloader(args,'train')
    _, val_generator = get_dataloader(args, 'val')
    if args.tt_list.lower() == 'none':
        test_generator = None 
    else:
        _, test_generator = get_dataloader(args, 'test')

    args.train_sampler=train_sampler

    solver = Solver(args=args,
                models = models,
                optimizer_g = optimizer_generator,
                optimizer_m = optimizer_mossformer,
                discriminators = discs,
                optimizer_discs = optimizer_discs,
                train_data = train_generator,
                validation_data = val_generator,
                test_data = test_generator
                ) 
    solver.train()


if __name__ == '__main__':
    parser = yamlargparse.ArgumentParser("Settings")
    
    # Log and Visulization
    parser.add_argument('--seed', dest='seed', type=int, default=20, help='the random seed')
    parser.add_argument('--config', help='config file path', action=yamlargparse.ActionConfigFile) 
    parser.add_argument('--config_json', type=str, help='Path to the config.json file')
    # experiment setting
    parser.add_argument('--mode', type=str, default='train', help='run train or inference')
    parser.add_argument('--use-cuda', dest='use_cuda', default=1, type=int, help='use cuda')
    parser.add_argument('--checkpoint_dir', type=str, default='checkpoints/FRCRN',help='the checkpoint dir')
    parser.add_argument('--network', type=str, default='frcrn', help='the model network types to be loaded for speech enhancment: frcrn, mossformer2')
    parser.add_argument('--train_from_last_checkpoint', type=int, help='0 or 1, whether to train from a pre-trained checkpoint, includes model weight, optimizer settings')
    #parser.add_argument('--init_checkpoint_path', type=str, default = None, help='pre-trained model path for initilizing the model weights for a new training')
    parser.add_argument('--print_freq', type=int, default=10, help='No. steps waited for printing info')
    parser.add_argument('--checkpoint_save_freq', type=int, default=50, help='No. steps waited for saving new checkpoint')
    parser.add_argument('--batch_size', type=int, help='Batch size')

    # dataset settings
    parser.add_argument('--tr-list', dest='tr_list', type=str, help='the train data list')
    parser.add_argument('--cv-list', dest='cv_list',type=str, help='the cross-validation data list')
    parser.add_argument('--tt-list', dest='tt_list',type=str, default=None, help='optional, the test data list')
    parser.add_argument('--accu_grad', type=int, help='whether to accumulate grad')
    parser.add_argument('--max_length', type=int, help='max_length of mixture in training')
    parser.add_argument('--input-path', dest='input_path', type=str, default=None, help='Path for low-resolution audio input for inference')
    #parser.add_argument('--num_workers', type=int, help='Number of workers to generate minibatch')
    #parser.add_argument('--sampling-rate', dest='sampling_rate', type=int, default=16000)
    parser.add_argument('--load_fbank', type=int, default=None, help='calculate and load fbanks for inputs')
    # FFT
    #parser.add_argument('--window-len',dest='win_len',type=int, help='the window-len in enframe')
    #parser.add_argument('--window-inc', dest='win_inc', type=int, default=100, help='the window include in enframe')
    #parser.add_argument('--fft-len', dest='fft_len', type=int, default=512, help='the fft length when in extract feature')
    #parser.add_argument('--window-type', dest='win_type', type=str, default='hamming', help='the window type in enframe, include hamming and None')
    #parser.add_argument('--num-mels', dest='num_mels', type=int, default=60, help='the number of mels when in extract feature')

    # optimizer
    parser.add_argument('--effec_batch_size', type=int, help='effective Batch size')
    parser.add_argument('--max-epoch', dest='max_epoch',type=int,default=20,help='the max epochs')
    parser.add_argument('--num-gpu', dest='num_gpu', type=int, default=1, help='the num gpus to use')
    #parser.add_argument('--init_learning_rate',  type=float, help='Init learning rate')
    #parser.add_argument('--finetune_learning_rate',  type=float, help='Finetune learning rate')
    parser.add_argument('--weight-decay', dest='weight_decay', type=float, default=0.00001)
    parser.add_argument('--clip-grad-norm', dest='clip_grad_norm', type=float, default=10.)
  
    # Distributed training
    parser.add_argument("--local-rank", dest='local_rank', type=int, default=0)

    

    #args = parser.parse_args()
    args, _ = parser.parse_known_args()
    json_config = load_config_json(args.config_json)
    args = combine_config_and_args(json_config, args)
    args = argparse.Namespace(**args)
    # check for single- or multi-GPU training
    args.distributed = False
    
    args.world_size = 1
    if 'WORLD_SIZE' in os.environ:
        args.distributed = int(os.environ['WORLD_SIZE']) > 1
        args.world_size = int(os.environ['WORLD_SIZE'])
    
    assert torch.backends.cudnn.enabled, "cudnn needs to be enabled"
    print(args)
    signal.signal(signal.SIGINT, cleanup)
    main(args)
