'''
Copyright (c) 2019 Uber Technologies, Inc.

Licensed under the Uber Non-Commercial License (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at the root directory of this project. 

See the License for the specific language governing permissions and
limitations under the License.
'''
'''
# TODO Add documentation
'''


__author__ = "Alexandros Papangelis"

from abc import ABC, abstractmethod
from copy import deepcopy


class ConversationalFrame(ABC):
    '''
    Provides a common type for Conversational Modules to communicate.
    '''

    def __init__(self, args=None):
        if not args:
            self.content = {}
        elif not isinstance(args, ConversationalFrame):
            self.content = deepcopy(args)
        else:
            self.content = deepcopy(args.content)


class ConversationalModule(ABC):
    '''
    Abstract class defining what it means to be a Conversational Module. These will be used primarily by the
    ConversationalGenericAgent.
    '''

    @abstractmethod
    def initialize(self, args):
        pass

    # Not necessary in stateless modules
    def receive_input(self, args):
        pass

    # This is used only to update internal state - there is no output
    def generic_receive_input(self, args: ConversationalFrame):
        if not isinstance(args, ConversationalFrame):
            args = ConversationalFrame(args)

        self.receive_input(args.content)

    @abstractmethod
    # Arguments may not be necessary for stateful modules
    def generate_output(self, args=None):
        pass

    def generic_generate_output(self, args):
        if isinstance(args, ConversationalFrame):
            args = args.content

        if not isinstance(args, dict):
            args = {'args': args}

        output = self.generate_output(args)

        if not isinstance(output, ConversationalFrame):
            output = ConversationalFrame(output)

        return output

    def at_terminal_state(self):
        # Default to False as this makes sense only for stateful modules
        # (i.e. doesn't make sense for a language generator)
        return False

    @abstractmethod
    def train(self, dialogue_episodes):
        pass

    @abstractmethod
    def load(self, path):
        pass

    @abstractmethod
    def save(self):
        pass
