import logging
from threading import Thread, Event
import time

from six.moves.queue import Queue
from six.moves import queue

from grab.spider.base_service import BaseService


class ParserService(BaseService):
    def __init__(self, spider, pool_size):
        self.spider = spider
        self.input_queue = Queue()
        self.pool_size = pool_size
        self.workers_pool = []
        for _ in range(self.pool_size):
            self.workers_pool.append(self.create_worker(self.worker_callback))
        self.supervisor = self.create_worker(self.supervisor_callback)
        self.register_workers(self.workers_pool, self.supervisor)

    def check_pool_health(self):
        to_remove = []
        for worker in self.workers_pool:
            if not worker.is_alive():
                self.spider.stat.inc('parser-thread-restore')
                new_worker = self.create_worker(self.worker_callback)
                self.workers_pool.append(new_worker)
                new_worker.start()
                to_remove.append(worker)
        for worker in to_remove:
            self.workers_pool.remove(worker)

    def supervisor_callback(self, worker):
        while True:
            worker.process_pause_signal()
            self.check_pool_health()
            time.sleep(1)

    def worker_callback(self, worker):
        process_request_count = 0
        try:
            work_permitted = True
            while work_permitted:
                worker.process_pause_signal()
                try:
                    result, task = self.input_queue.get(True, 0.1)
                except queue.Empty:
                    pass
                else:
                    worker.is_busy_event.set()
                    try:
                        process_request_count += 1
                        try:
                            handler = self.spider.find_task_handler(task)
                        except NoTaskHandler as ex:
                            ex.tb = format_exc()
                            self.spider.task_dispatcher.input_queue.put((ex, task))
                            self.spider.stat.inc('parser:handler-not-found')
                        else:
                            self.spider.process_network_result(
                                result, task, handler,
                            )
                            self.spider.stat.inc('parser:handler-processed')
                        if self.spider.parser_requests_per_process:                    
                            if (process_request_count >=                        
                                    self.spider.parser_requests_per_process):          
                                work_permitted = False  
                    finally:
                        worker.is_busy_event.clear()
        except Exception as ex:
            logging.error('', exc_info=ex)
            raise