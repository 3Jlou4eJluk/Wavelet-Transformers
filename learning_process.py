import numpy as np 
import torch
import tqdm
import pickle
import time
from functools import partial



class ModelCheckpoint:
    def __init__(self, filepath=None):
        self.val_acc = None
        self.val_loss = None
        self.train_acc = None
        self.train_loss = None
        self.filepath = filepath
        if filepath is None:
            print('<Model Checkpoint>: Warning, model save path is not specified. Setting current directory.')
            self.filepath = '.'
    
    def update(self, model, train_acc, train_loss, val_acc, val_loss):
        if (self.val_acc is None) or (val_acc > self.val_acc):
            self.val_acc = val_acc
            self.val_loss = val_loss
            self.train_acc = train_acc
            self.train_loss = train_loss
            self.save(model)
    

    def save(self, model):
        torch.save(model, f"{self.filepath}/model_save.pt")
        with open(f'{self.filepath}/model_checkpoint.pkl', 'wb') as f:
            pickle.dump(self, f)
    
    def load(self):
        with open(f'{self.filepath}/model_checkpoint.pkl', 'rb') as f:
            loaded_object = pickle.load(f)
            self.val_acc = loaded_object.val_acc
            self.val_loss = loaded_object.val_loss
            self.train_acc = loaded_object.train_acc
            self.train_loss = loaded_object.train_loss
        return torch.load(f"{self.filepath}/model_save.pt")

    def __repr__(self):
        return f"<Model Checkpoint>\n"\
             + f"Validation Accuracy: {self.val_acc}\n"\
             + f"Validation Loss: {self.val_loss}\n"\
             + f"Train Accuracy: {self.train_acc}\n"\
             + f"Train Loss: {self.train_loss}\n"
        


class Preprocessor:
    def __init__(self, objects, labels, *funcs):
        self.objects = objects
        self.labels = labels
        self.funcs = [func for func in funcs]

        self.preprocessed_objects = None
    
    def action(self):
        self.preprocessed_objects = self.objects

        # Preprocessing data
        for f in self.funcs:
            self.preprocessed_objects = f(self.preprocessed_objects)
        return self.preprocessed_objects


class Learner:
    def __init__(
            self, model, optimizer, loss_fn, scheduler, 
            train_dl, val_dl, device, epochs, checkpoint_path=None,
            max_training_time=None, chill_time=120
        ):
        self.metrics = {
            "train_loss": [],
            "val_loss": [],
            "train_acc": [],
            "val_acc": [],
            "train_loss_epoch_no": [],
            "val_loss_epoch_no": []
        }

        self.model = model
        self.optimizer = optimizer
        self.loss_fn = loss_fn
        self.scheduler = scheduler
        self.checkpoint_path = checkpoint_path
        self.max_training_time = max_training_time
        self.chill_time = chill_time

        self.train_dl = train_dl
        self.val_dl = val_dl

        self.epochs = epochs
        self.device = device


        # service fields
        self.model_checkpoint = ModelCheckpoint(self.checkpoint_path)
        self.current_work_time = 0

    def train_epoch(self, log_train_quality=False, verbose=False, epoch_no=None):
        self.model.train()

        loss_sum = []
        acc_sum = []
        for x, y in tqdm(self.train_dl, disable=not verbose, leave=True):
            start_time = time.time()
            if 'cpu' not in self.device:
                x = x.to(self.device)
                y = y.to(self.device)
            def closure(x, y, loss_sum, acc_sum):
                self.optimizer.zero_grad()
                pred = self.model.forward(x)
                loss = self.loss_fn(pred, y.squeeze())
                loss.backward()

                pred_classes = torch.argmax(pred, dim=1)
                loss_sum.append(float(loss.item()))
                acc_sum.append(float((
                    pred_classes.detach().squeeze() == y.squeeze()
                ).sum() / len(y)))
            
            closure = partial(closure, x, y, loss_sum, acc_sum)
            self.optimizer.step(closure)
            if self.scheduler is not None:
                self.scheduler.step()
            self.current_work_time += time.time() - start_time
            if (self.max_training_time is not None) and (current_work_time >= self.max_training_time):
                current_work_time = 0
                time.sleep(self.chill_time)
    
        loss_sum_val = sum(loss_sum) / len(self.train_dl)
        acc_sum_val = sum(acc_sum) / len(self.train_dl)

        if log_train_quality and (epoch_no is not None):
            self.metrics['train_loss_epoch_no'].append(epoch_no)
            self.metrics['train_loss'].append(loss_sum_val)
            self.metrics['train_acc'].append(acc_sum_val)


    def validation_epoch(self, log_train_quality=False, verbose=False, epoch_no=None):
        self.model.eval()

        train_loss = 0.
        val_loss = 0.
        train_acc = 0.
        val_acc = 0.
        with torch.no_grad():
            if log_train_quality:
                for x, y in tqdm(self.train_dl, disable=not verbose, leave=True):
                    if 'cpu' not in self.device:
                        x = x.to(self.device)
                        y = y.to(self.device)
                    pred = self.model.forward(x)
                    pred_classes = torch.argmax(pred, dim=1)
                    train_acc += float((
                        pred_classes.detach().squeeze() == y.squeeze()
                    ).sum() / len(y))

                    loss = self.loss_fn(pred, y.squeeze())
                    train_loss += float(loss.item())
                train_loss /= len(self.train_dl)
                train_acc /= len(self.train_dl)
            
            for x, y in tqdm(self.val_dl, disable=not verbose):
                if 'cpu' not in self.device:
                    x = x.to(self.device)
                    y = y.to(self.device)
                pred = self.model.forward(x)
                pred_classes = torch.argmax(pred, dim=1)
                val_acc += float((
                    pred_classes.detach().squeeze() == y.squeeze()
                ).sum() / len(y))
                
                loss = self.loss_fn(pred, y.squeeze())
                val_loss += float(loss.item())
            
            val_acc /= len(self.val_dl)
            val_loss /= len(self.val_dl)
        
        if epoch_no is not None:
            if log_train_quality:
                self.metrics['train_loss_epoch_no'].append(epoch_no)
                self.metrics['train_loss'].append(train_loss)
                self.metrics['train_acc'].append(train_acc)

            self.metrics['val_loss_epoch_no'].append(epoch_no)
            self.metrics['val_loss'].append(val_loss)
            self.metrics['val_acc'].append(val_acc)
            self.model_checkpoint.update(
                self.model, train_acc, train_loss, 
                val_acc, val_loss
            )

    def train_cycle(self):
        for epoch in (pbar := tqdm(range(self.epochs), total=self.epochs, disable=False)):
            self.train_epoch(log_train_quality=True, verbose=True, epoch_no=epoch)
            if not epoch % 1:
                self.validation_epoch(log_train_quality=False, verbose=True, epoch_no=epoch)
                pbar.set_description(('Loss (Train/Test): {0:.3f}/{1:.3f}.\n' +\
                                     'Accuracy,% (Train/Test): {2:.2f}/{3:.2f}.\n' +\
                                     'Update Epoch: {4}').format(
                    self.metrics['train_loss'][-1], self.metrics['val_loss'][-1], 
                    self.metrics['train_acc'][-1], self.metrics['val_acc'][-1],
                    epoch
                ))

    def action(self):
        if 'cpu' not in self.device:
            self.model.to(self.device)
        self.train_cycle()


class BruteForcer:
    def __init__(self):
        pass

