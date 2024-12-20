import os
from concurrent.futures import ThreadPoolExecutor

import jsonlines
import json
from datetime import datetime
from dataclasses import dataclass
import numpy as np
from tqdm import tqdm

from rag.dataset.utils import load_jsonlines
from rag.dataset.metrics import match, exact_match, F1
from rag.dataset.base_dataset import MultiChoiceQA
from rag.dataset.utils import get_args_form_config
from openai import OpenAI
from typing import Union


class PubHealth(MultiChoiceQA):
    def __init__(self, args):
        self.args = args
        self.print_fn = getattr(args, 'print_fn', print)
        self.file_name = getattr(args, 'file_name', '')
        self.time = getattr(args, 'time', None)
        self.output_file = args.output_dir
        self.config = getattr(args, 'config', None)
        with open(args.api_key_path, 'r') as f:
            api_key = f.readline().strip()
        self.client = OpenAI(api_key=api_key)
        super().__init__(args)

    @dataclass
    class InputStruction:
        question = 'question'
        answer = 'answers'
        pregiven_passages = 'ctxs'

    @dataclass
    class OutputStruction:
        question = 'question'
        answer = 'answers'
        generation = 'generation'

    def load_dataset(self)-> list[dict]:
        if self.eval_datapath.endswith(".json"):
            eval_dataset = json.load(open(self.eval_datapath))
        else:
            eval_dataset = load_jsonlines(self.eval_datapath)
        return eval_dataset

    def save_result(self, inference_result: list[dict])-> None: 
        '''
        save rag inference results
        '''
        self.print_fn('storing inference result....')
        file_name = 'rag_output-' + self.file_name + f'time={self.time}.jsonl'
        output_file = os.path.join(self.output_dir, file_name)
        with jsonlines.open(output_file, 'w') as outfile: 
            outfile.write_all(inference_result)
        self.print_fn(f'output file path:{output_file}')
        self.print_fn('success!')
    
    def save_evaluation_results(self, eval_results:dict[str,float]) -> None:
        '''
        save evaluation results and all args
        '''
        args_dict = get_args_form_config(self.config)
        eval_results.update(args_dict)
        file_name = 'rag_output-' + self.file_name + f'time={self.time}.jsonl.evaluation'  
        output_file = os.path.join(self.output_dir, file_name)
        with jsonlines.open(output_file, 'w') as outfile: 
            outfile.write_all([eval_results])
        self.print_fn(f'evaluation file path:{output_file}')
        self.print_fn('success!')

    def record_result(self, eval_data:dict, final_prediction:str, inference_results:list) -> list[dict]:
        inference_results.append(
            {
             self.OutputStruction.question: eval_data[self.InputStruction.question],
             self.OutputStruction.answer: eval_data[self.InputStruction.answer],
             self.OutputStruction.generation: final_prediction
            })
        return inference_results

    def eval_acc(self, infer_results: list[dict]):
        eval_results = []
        for idx, data in enumerate(infer_results):
            if type(data[self.OutputStruction.answer]) is str:
                answer = [data[self.OutputStruction.answer]]
            elif type(data[self.OutputStruction.answer]) is list:
                answer = data[self.OutputStruction.answer]
            elif type(data[self.OutputStruction.answer]) is bool: # The answer of StrategyQA is bool
                answer = [str(data[self.OutputStruction.answer])]
            elif data[self.OutputStruction.answer] is None:
                return 'No answer in dataset'
            else:
                raise InvalidAnswerType("The type of answer is invalid. Only str and list[str] is valid. Check the answer in your raw data.")
            metric_result = match(data[self.OutputStruction.generation], answer)
            eval_results.append(metric_result)
        return np.mean(eval_results)
    
    def eval_exact_match(self, infer_results: list[dict]) -> float:
        eval_reaults = []
        for _, data in enumerate(infer_results):
            if type(data[self.OutputStruction.answer]) is str:
                answer = [data[self.OutputStruction.answer]]
            elif type(data[self.OutputStruction.answer]) is list:
                answer = data[self.OutputStruction.answer]
            elif type(data[self.OutputStruction.answer]) is bool: # The answer of StrategyQA is bool
                answer = [str(data[self.OutputStruction.answer])]
            elif data[self.OutputStruction.answer] is None:
                return 'No answer in dataset'
            else:
                raise InvalidAnswerType("The type of answer is invalid. Only str and list[str] is valid. Check the answer in your raw data.")
            metric_result = exact_match(data[self.OutputStruction.generation], answer)
            eval_reaults.append(metric_result)
        return float(np.mean(eval_reaults))

    def eval_f1_score(self, infer_results: list[dict]) -> float:
        eval_reaults = []
        for _, data in enumerate(infer_results):
            if type(data[self.OutputStruction.answer]) is str:
                answer = [data[self.OutputStruction.answer]]
            elif type(data[self.OutputStruction.answer]) is list:
                answer = data[self.OutputStruction.answer]
            elif type(data[self.OutputStruction.answer]) is bool: # The answer of StrategyQA is bool
                answer = [str(data[self.OutputStruction.answer])]
            elif data[self.OutputStruction.answer] is None:
                return 'No answer in dataset'
            else:
                raise InvalidAnswerType("The type of answer is invalid. Only str and list[str] is valid. Check the answer in your raw data.")
            metric_result = F1(data[self.OutputStruction.generation], answer)
            eval_reaults.append(metric_result)
        return float(np.mean(eval_reaults))

    def llm_judge(self, infer_results: list[dict]) -> Union[float, str]:
        def process_data(data):
            if type(data[self.OutputStruction.answer]) is str:
                answer = data[self.OutputStruction.answer]
            elif type(data[self.OutputStruction.answer]) is list:
                answer = data[self.OutputStruction.answer][0]
            elif type(data[self.OutputStruction.answer]) is bool: # The answer of StrategyQA is bool
                answer = str(data[self.OutputStruction.answer])
            elif data[self.OutputStruction.answer] is None:
                return 'No answer in dataset'
            else:
                raise InvalidAnswerType("The type of answer is invalid. Only str and list[str] is valid. Check the answer in your raw data.")
            if answer=='':
                return False
            question=data[self.OutputStruction.question]
            generation=data[self.OutputStruction.generation]
            judge_result=self.call_llm(question, answer, generation)
            return judge_result
        with ThreadPoolExecutor(100) as executor:
            eval_reaults=list(tqdm(executor.map(process_data, infer_results)))
        return eval_reaults.count(True)/len(eval_reaults)

    def call_llm(self, query, answer, generation):
        prompt = f'Given a question, determine whether the two answers are consistent, and output True or False.\nQuestion: {query}\nAnswer 1: {answer}\nAnswer 2: {generation}\nResult:'
        result=self.client.chat.completions.create(model='gpt-4o-mini',
                                                   messages=[{'role':'user','content':prompt}],
                                                   temperature=0)
        result=result.choices[0].message.content
        if 'true' in result.lower().strip():
            return True
        else:
            return False

class InvalidAnswerType(Exception):
    pass