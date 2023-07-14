"""
Bitcoin Solo Miner
@author Morgiver

Inspired by:
https://github.com/iceland2k14/solominer
https://github.com/guerrerocarlos/bitcoin-miner/
https://www.righto.com/2014/02/bitcoin-mining-hard-way-algorithms.html
https://braiins.com/stratum-v1/docs 
"""
import os
import time
import json
import socket
import requests
import threading

import random
import hashlib
import binascii

from constant import *

class Job:
    """
    Job class

    Description:
        Job class is constructed to manage one job at a time.
        It use threading to process multiple nonce calculation in parallel.
    """
    def __init__(self, job_id, previous_hash, coinbase_one, coinbase_two, merkle_tree, version, nbits, ntime, clean_jobs, extra_nonce_one, extra_nonce_two_size) -> None:
        self.job_id               = job_id 
        self.previous_hash        = previous_hash
        self.coinbase_one         = coinbase_one
        self.coinbase_two         = coinbase_two
        self.merkle_tree          = merkle_tree
        self.version              = version 
        self.nbits                = nbits 
        self.ntime                = ntime 
        self.clean_jobs           = clean_jobs
        self.target               = (self.nbits[2:] + '00' * (int(self.nbits[:2], 16) - 3)).zfill(64)
        self.extra_nonce_one      = extra_nonce_one
        self.extra_nonce_two      = None
        self.extra_nonce_two_size = extra_nonce_two_size

        self.threads        = []
        self.nonces         = []
        self.is_mining      = False
        self.solution_found = False

    def get_latest_block(self) -> str:
        """
        Returns the latest block
        """
        return requests.get(LATEST_BLOCK_ADDRESS).json()
    
    def get_block_height(self) -> int:
        """
        Returns the height of latest block
        """
        return int(self.get_latest_block()['height'])

    def add_nonce(self, job_id, hash, nonce) -> None:
        """
        Add a Nonce processed by a thread

        Arguments:
            job_id (int): The ID of the Job given by the pool
            hash (bytes): The hash generated with the nonce
            nonce  (str): The random nonce
        """
        self.nonces.append((job_id, hash, nonce))
        if len(self.nonces) > 10:
            self.nonces.pop(0)

    def mine(self, bm, rand_range) -> None:
        """
        Execute the search of the good Nonce

        Arguments:
            bm (BitcoinMiner) : Instance of the BitcoinMiner
            rand_range (tuple): The range of search used to generate a random nonce
        """
        self.extra_nonce_two = hex(random.randint(rand_range[0], rand_range[1]-1))[2:].zfill(2 * self.extra_nonce_two_size)
        coinbase             = self.coinbase_one + self.extra_nonce_one + self.extra_nonce_two + self.coinbase_two
        merkle_root          = hashlib.sha256(hashlib.sha256(binascii.unhexlify(coinbase)).digest()).digest()

        for tx_hash in self.merkle_tree:
            merkle_root = hashlib.sha256(hashlib.sha256(merkle_root + binascii.unhexlify(tx_hash)).digest()).digest()

        merkle_root = binascii.hexlify(merkle_root).decode() 
        merkle_root = ''.join([merkle_root[i] + merkle_root[i + 1] for i in range(0, len(merkle_root), 2)][::-1])

        self.is_mining = True

        while not self.solution_found and self.is_mining:
            nonce       = hex(random.randint(0, 2**32 - 1))[2:].zfill(8)
            blockheader = self.version + self.previous_hash + merkle_root + self.ntime + self.nbits + nonce + '000000800000000000000000000000000000000000000000000000000000000000000000000000000000000080020000'
            hash        = hashlib.sha256(hashlib.sha256(binascii.unhexlify(blockheader)).digest()).digest()
            hash        = binascii.hexlify(hash).decode()

            if hash < self.target:
                self.solution_found = True
                bm.submit(self.job_id, self.extra_nonce_two, self.ntime, nonce)

    def start_mining(self, bm, nbr_threads=4) -> None:
        """
        Start the process of mining.
        Every thread will take a range of integer to generate nonce in that specific range to split the work
        between threads.

        Arguments:
            bm (BitcoinMiner): Actual instance of BitcoinMiner
            nbr_threads (int): The number of threads to use
        """
        max_rand = 2**32
        step_rand = max_rand / nbr_threads
        start = 0
        i = 1

        for _ in range(nbr_threads):
            self.threads.append(threading.Thread(target=self.mine, args=(bm, [int(start), int(step_rand * i)]))) 
            self.threads[-1].start()
            i += 1
            start += step_rand

    def stop_mining(self) -> None:
        """
        Stop the process of mining and its related threads
        """
        self.is_mining = False
        for thread in self.threads:
            thread.join()

class Request:
    """
    Just an object to modelize a Request and its callback function
    """
    def __init__(self, id, fnc) -> None:
        self.id = id
        self.executed = False
        self.fnc = fnc

class BitcoinMiner:
    def __init__(self, bitcoin_address: str) -> None:
        self.socket               = None
        self.bitcoin_address      = bitcoin_address
        self.client_thread        = None
        self.requests             = []
        self.difficulty           = None
        self.sub_details          = None
        self.extra_nonce_one      = None 
        self.extra_nonce_two_size = None
        self.jobs                 = []
        self.last_update          = None
        self.errors               = []
        self.is_listening         = False
    
    def add_error(self, error, message) -> None:
        """ Staking an error with its related server message if there is """
        self.errors.append((error, message))

    def payload_builder(self, method: str, params: list = []) -> str:
        """
        Build a payload for a request to send to the pool

        Arguments:
            method  (str): The method used
            params (list): The parameters to send in the method
        """
        payload = {
            "id": len(self.requests) - 1,
            "params": params,
            "method": method
        }
        
        return bytes(f'{json.dumps(payload)}\n', 'utf-8')

    def request(self, callback, method: str, params: list = []) -> None:
        """ 
        Send a request to the pool

        Arguments:
            callback (function): The function to callback if the server response
            method        (str): The method to use by pool
            params       (list): The parameters to use with the method
        """
        req_id = len(self.requests)
        self.requests.append(Request(req_id, callback))

        if self.socket is not None:
            self.socket.sendall(self.payload_builder(method, params))
        else:
            raise Exception('Socket is not defined')

    def handle_new_job(self, data) -> None:
        """
        Handling a new job when the server send one

        Arguments:
            data (dict): Information of the job sended by the pool 
        """
        if self.extra_nonce_one is not None and self.extra_nonce_two_size is not None:
            job_id, previous_hash, coinbase_one, coinbase_two, merkle_tree, version, nbits, ntime, clean_jobs = data['params']

            if len(self.jobs) > 0 and self.jobs[-1].is_mining and previous_hash != self.jobs[-1].previous_hash:
                self.jobs[-1].stop_mining()
            
            if len(self.jobs) < 1 or (len(self.jobs) > 0 and previous_hash != self.jobs[-1].previous_hash):
                self.jobs.append(Job(job_id, previous_hash, coinbase_one, coinbase_two, merkle_tree, version, nbits, ntime, clean_jobs, self.extra_nonce_one, self.extra_nonce_two_size))
                self.jobs[-1].start_mining(self)

    def handle_server_message(self, message) -> None:
        """
        Handling server message. 
        It will use the 'method' column to know to do with the message.
        If the message contain an id, it's a response to a previous request.

        Arguments:
            message (dict): The message sended by the pool
        """
        if 'id' in message and message['id'] is not None:
            self.requests[message['id']].fnc(message)

        if 'method' in message and message['method'] == 'mining.notify':
            self.handle_new_job(message)
        
        if 'method' in message and message['method'] == 'mining.authorize':
            self.callback_authorize(message)

        if 'method' in message and message['method'] == 'mining.set_difficulty':
            self.difficulty = message['params'][0]
    
    def listen_server(self) -> None:
        """
        Listening the server to catch every message sended by the pool
        """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((SOLO_POOL_HOST, SOLO_POOL_PORT))

        while self.is_listening:
            data = self.socket.recv(SOCKET_BYTES)

            while not data.endswith(b'\n'):
                data += self.socket.recv(SOCKET_BYTES)

            try:
                self.handle_server_message(json.loads(data.decode().split('\n')[0]))
                self.update_screen()
            except json.decoder.JSONDecodeError as e:
                self.add_error(e, data)

    def start(self) -> None:
        """
        Starting a new thread to listen to the pool messages.
        """
        self.is_listening  = True
        self.client_thread = threading.Thread(target=self.listen_server, args=())
        self.client_thread.start()
    
    def stop(self) -> None:
        """
        Stopping the listening
        """
        self.jobs[-1].stop_mining()
        self.is_listening = False
        self.client_thread.join()

    def callback_subscribe(self, message) -> None:
        """ Callback function for the request mining.subscribe """
        self.sub_details, self.extra_nonce_one, self.extra_nonce_two_size = message['result']

    def callback_authorize(self, message) -> None:
        """ Callback function for the request mining.authorize """
        pass

    def callback_submit(self, message) -> None:
        """ Callback function for the request mining.submit """
        pass

    def subscribe(self) -> None:
        """
        Subscribe Request
        """
        try:
            self.request(self.callback_subscribe, 'mining.subscribe', [])
        except Exception as e:
            self.add_error(e, "")

    def authorize(self) -> None:
        """
        Authorize Request
        """
        try:
            self.request(self.callback_authorize, 'mining.authorize', [self.bitcoin_address, "password"])
        except Exception as e:
            self.add_error(e, "")

    def submit(self, job_id, extra_nonce_two, ntime, nonce) -> None:
        """
        Submit Request will send information about a Job that found the good nonce
        """
        try:
            self.request(self.callback_submit, 'mining.submit', [self.bitcoin_address, job_id, extra_nonce_two, ntime, nonce])
        except Exception as e:
            self.add_error(e, "")

    def update_screen(self) -> None:
        """
        Updating the screen to show information about actual job, target, extra nonce and btc address
        """
        prev_hash = None
        target    = None

        if len(self.jobs) > 0:
            prev_hash = self.jobs[-1].previous_hash
            target    = self.jobs[-1].target

        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"----------------- Bitcoin Solo Miner -----------------")
        print(f"Mining for : {self.bitcoin_address} | Previous Hash: {prev_hash} | Extra Nonce: {self.extra_nonce_one}")
        print(f"Actual Target : {target}")
        print(f"Last Error :")
        if len(self.errors) > 0:
            print("Error :")
            print(self.errors[-1][0])
            print("Associated Server message:")
            print(self.errors[-1][1])

if __name__ == '__main__':
    bm = BitcoinMiner('bc1qs3262jusaazr2gj8dkshwgndxcaz3ku2t608ye') # <--- DONT FORGET TO CHANGE THE BTC ADDRESS
    bm.start()
    time.sleep(2)
    bm.subscribe()
    bm.authorize()
    