#!/usr/bin/env node

// Simple Node.js test runner for TodoApp
// This creates a minimal DOM environment and runs tests

// Mock DOM elements for Node.js
const mockDOM = {
    elements: {},
    createElement: function(tag) {
        const el = {
            tagName: tag,
            addEventListener: () => {},
            appendChild: () => {},
            replaceWith: () => {},
            classList: { toggle: () => {}, add: () => {}, remove: () => {} },
            setAttribute: () => {},
            getAttribute: () => {},
            textContent: '',
            value: '',
            innerHTML: '',
            style: {}
        };
        return el;
    },
    getElementById: function(id) {
        return this.elements[id] || this.createElement('input');
    },
    querySelector: function(sel) {
        return this.createElement('div');
    },
    querySelectorAll: function(sel) {
        return [];
    }
};

// Setup minimal globals
global.document = {
    ...mockDOM,
    addEventListener: () => {},
    readyState: 'complete',
    querySelector: (sel) => mockDOM.querySelector(sel),
    querySelectorAll: (sel) => mockDOM.querySelectorAll(sel),
    getElementById: (id) => mockDOM.getElementById(id)
};

global.localStorage = {
    data: {},
    getItem: function(key) {
        return this.data[key] || null;
    },
    setItem: function(key, value) {
        this.data[key] = value;
    },
    removeItem: function(key) {
        delete this.data[key];
    }
};

// Load the TodoApp code
const fs = require('fs');
const vm = require('vm');
let appCode = fs.readFileSync('/app/workspace/todo-web-v1-K-005/app.js', 'utf8');

// Remove the DOMContentLoaded listener to prevent instantiation during load
appCode = appCode.replace(/document\.addEventListener\('DOMContentLoaded'[\s\S]*?\}\);/, '');

// Create a context with the necessary globals and module
const context = vm.createContext({
    document: global.document,
    localStorage: global.localStorage,
    Date: global.Date,
    console: global.console,
    module: { exports: {} }
});

// Run the code in the context and export TodoApp
const wrappedCode = appCode + '\nif (typeof module !== "undefined" && module.exports) { module.exports.TodoApp = TodoApp; }';
vm.runInContext(wrappedCode, context);

// Extract TodoApp from context
const TodoApp = context.module.exports.TodoApp;

// Test runner
function runTests() {
    console.log('=== Todo App Test Suite ===\n');

    let passed = 0;
    let failed = 0;
    const results = [];

    function test(name, fn) {
        try {
            // Clear localStorage before each test
            global.localStorage.data = {};
            fn();
            passed++;
            results.push({ name, passed: true, error: null });
            console.log(`✓ ${name}`);
        } catch (e) {
            failed++;
            results.push({ name, passed: false, error: e.message });
            console.log(`✗ ${name}: ${e.message}`);
        }
    }

    // Test 1
    test('TodoApp initializes with correct properties', () => {
        const app = new TodoApp();
        if (app.todos === undefined) throw new Error('todos undefined');
        if (app.currentFilter !== 'all') throw new Error('currentFilter not "all"');
    });

    // Test 2
    test('addTodo increases todo count', () => {
        const app = new TodoApp();
        const initialLength = app.todos.length;
        app.addTodo('Test todo');
        if (app.todos.length !== initialLength + 1) {
            throw new Error(`Expected ${initialLength + 1}, got ${app.todos.length}`);
        }
    });

    // Test 3
    test('addTodo rejects empty strings', () => {
        const app = new TodoApp();
        const initialLength = app.todos.length;
        app.addTodo('   ');
        if (app.todos.length !== initialLength) {
            throw new Error('Empty string was added');
        }
    });

    // Test 4
    test('toggleTodo changes completed status', () => {
        const app = new TodoApp();
        app.addTodo('Test todo');
        const todo = app.todos[0];
        const initialCompleted = todo.completed;
        app.toggleTodo(todo.id);
        if (app.todos[0].completed === initialCompleted) {
            throw new Error('Status did not toggle');
        }
    });

    // Test 5
    test('getActiveCount returns correct value', () => {
        const app = new TodoApp();
        app.todos = [];
        app.addTodo('Active 1');
        app.addTodo('Active 2');
        app.addTodo('Completed');
        app.toggleTodo(app.todos[2].id);
        const count = app.getActiveCount();
        if (count !== 2) throw new Error(`Expected 2, got ${count}`);
    });

    // Test 6
    test('getCompletedCount returns correct value', () => {
        const app = new TodoApp();
        app.todos = [];
        app.addTodo('Active 1');
        app.addTodo('Completed 1');
        app.addTodo('Completed 2');
        app.toggleTodo(app.todos[1].id);
        app.toggleTodo(app.todos[2].id);
        const count = app.getCompletedCount();
        if (count !== 2) throw new Error(`Expected 2, got ${count}`);
    });

    // Test 7
    test('deleteTodo removes items', () => {
        const app = new TodoApp();
        app.addTodo('Test todo');
        const todo = app.todos[0];
        const initialLength = app.todos.length;
        app.deleteTodo(todo.id);
        if (app.todos.length !== initialLength - 1) {
            throw new Error('Item was not deleted');
        }
    });

    // Test 8
    test('getFilteredTodos returns all items by default', () => {
        const app = new TodoApp();
        app.todos = [];
        app.addTodo('Item 1');
        app.addTodo('Item 2');
        const filtered = app.getFilteredTodos();
        if (filtered.length !== 2) throw new Error(`Expected 2, got ${filtered.length}`);
    });

    // Test 9
    test('clearCompleted removes completed items', () => {
        const app = new TodoApp();
        app.todos = [];
        app.addTodo('Active');
        app.addTodo('Completed');
        app.toggleTodo(app.todos[1].id);
        const initialLength = app.todos.length;
        app.clearCompleted();
        if (app.todos.length !== initialLength - 1 || app.getCompletedCount() !== 0) {
            throw new Error('clearCompleted failed');
        }
    });

    // Test 10
    test('setFilter changes currentFilter', () => {
        const app = new TodoApp();
        app.setFilter('active');
        if (app.currentFilter !== 'active') {
            throw new Error('Filter was not changed');
        }
    });

    // Test 11
    test('getFilteredTodos filters by active status', () => {
        const app = new TodoApp();
        app.todos = [];
        app.addTodo('Active');
        app.addTodo('Completed');
        app.toggleTodo(app.todos[1].id);
        app.setFilter('active');
        const filtered = app.getFilteredTodos();
        if (filtered.length !== 1) throw new Error(`Expected 1, got ${filtered.length}`);
    });

    // Test 12
    test('getFilteredTodos filters by completed status', () => {
        const app = new TodoApp();
        app.todos = [];
        app.addTodo('Active');
        app.addTodo('Completed');
        app.toggleTodo(app.todos[1].id);
        app.setFilter('completed');
        const filtered = app.getFilteredTodos();
        if (filtered.length !== 1) throw new Error(`Expected 1, got ${filtered.length}`);
    });

    // Test 13
    test('saveTodos persists data to localStorage', () => {
        global.localStorage.data = {};
        const app = new TodoApp();
        app.todos = [{ id: 1, text: 'Test', completed: false }];
        app.saveTodos();
        const saved = global.localStorage.getItem('todos');
        if (!saved) throw new Error('Data was not saved');
        const parsed = JSON.parse(saved);
        if (parsed.length !== 1) throw new Error('Data was corrupted');
    });

    // Test 14
    test('loadTodos retrieves data from localStorage', () => {
        global.localStorage.data = { todos: JSON.stringify([{ id: 1, text: 'Test', completed: false }]) };
        const app = new TodoApp();
        if (app.todos.length !== 1) throw new Error('Data was not loaded');
        if (app.todos[0].text !== 'Test') throw new Error('Data was corrupted');
    });

    // Summary
    console.log('\n=== Results ===');
    console.log(`Passed: ${passed}`);
    console.log(`Failed: ${failed}`);
    console.log(`Total: ${passed + failed}`);

    if (failed === 0) {
        console.log('\n✓ All tests passed!');
        process.exit(0);
    } else {
        console.log('\n✗ Some tests failed!');
        process.exit(1);
    }
}

runTests();
