class TodoApp {
    constructor() {
        this.STORAGE_KEY = 'todos';
        this.todos = [];
        this.currentFilter = 'all';

        // Only setup DOM if in browser
        if (typeof document !== 'undefined' && document.getElementById) {
            this.setupDOM();
            this.setupEventListeners();
            this.init();
        }
    }

    setupDOM() {
        this.todoInput = document.getElementById('new-todo-input');
        this.todosList = document.getElementById('todos-list');
        this.todoCountEl = document.querySelector('[data-testid="todo-count"]');
        this.clearCompletedBtn = document.getElementById('clear-completed-btn');
        this.filterButtons = document.querySelectorAll('.filter-btn');
    }

    setupEventListeners() {
        this.todoInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.addTodo(this.todoInput.value);
            }
        });

        this.filterButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                this.setFilter(btn.getAttribute('data-filter'));
            });
        });

        this.clearCompletedBtn.addEventListener('click', () => {
            this.clearCompleted();
        });
    }

    init() {
        this.loadTodos();
        this.renderTodos();
        this.updateCounter();
    }

    loadTodos() {
        const stored = localStorage.getItem(this.STORAGE_KEY);
        this.todos = stored ? JSON.parse(stored) : [];
    }

    saveTodos() {
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(this.todos));
    }

    addTodo(text) {
        const trimmed = text.trim();
        if (trimmed === '') return;

        this.todos.push({
            id: Date.now(),
            text: trimmed,
            completed: false
        });

        this.saveTodos();
        if (this.todoInput) {
            this.todoInput.value = '';
        }
        this.renderTodos();
        this.updateCounter();
    }

    toggleTodo(id) {
        const todo = this.todos.find(t => t.id === id);
        if (todo) {
            todo.completed = !todo.completed;
            this.saveTodos();

            if (typeof document !== 'undefined' && document.activeElement) {
                const focusedEl = document.activeElement;
                const wasFocusInTodo = focusedEl?.closest('[data-testid="todo-item"]');
                let focusType = null;

                if (wasFocusInTodo) {
                    if (focusedEl.classList?.contains('todo-checkbox')) {
                        focusType = 'checkbox';
                    } else if (focusedEl.classList?.contains('todo-delete-btn')) {
                        focusType = 'button';
                    }
                }

                this.renderTodos();
                this.updateCounter();

                if (focusType && wasFocusInTodo) {
                    const todoItem = document.querySelector(`[data-testid="todo-item"]`);
                    if (todoItem) {
                        if (focusType === 'checkbox') {
                            todoItem.querySelector('.todo-checkbox')?.focus();
                        } else if (focusType === 'button') {
                            todoItem.querySelector('.todo-delete-btn')?.focus();
                        }
                    }
                }
            } else {
                this.renderTodos();
                this.updateCounter();
            }
        }
    }

    deleteTodo(id) {
        this.todos = this.todos.filter(t => t.id !== id);
        this.saveTodos();
        this.renderTodos();
        this.updateCounter();
    }

    clearCompleted() {
        this.todos = this.todos.filter(t => !t.completed);
        this.saveTodos();
        this.renderTodos();
        this.updateCounter();
    }

    getFilteredTodos() {
        switch (this.currentFilter) {
            case 'active':
                return this.todos.filter(t => !t.completed);
            case 'completed':
                return this.todos.filter(t => t.completed);
            default:
                return this.todos;
        }
    }

    getActiveCount() {
        return this.todos.filter(t => !t.completed).length;
    }

    getCompletedCount() {
        return this.todos.filter(t => t.completed).length;
    }

    renderTodos() {
        if (!this.todosList) return;

        this.todosList.innerHTML = '';

        const filtered = this.getFilteredTodos();

        filtered.forEach(todo => {
            const li = document.createElement('li');
            li.setAttribute('data-testid', 'todo-item');
            li.className = 'todo-item' + (todo.completed ? ' completed' : '');

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'todo-checkbox';
            checkbox.checked = todo.completed;
            checkbox.setAttribute('aria-label', `Complete: ${todo.text}`);
            checkbox.addEventListener('change', () => this.toggleTodo(todo.id));

            const textSpan = document.createElement('span');
            textSpan.className = 'todo-text';
            textSpan.textContent = todo.text;

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'todo-delete-btn';
            deleteBtn.textContent = 'Delete';
            deleteBtn.setAttribute('aria-label', 'Delete');
            deleteBtn.addEventListener('click', () => this.deleteTodo(todo.id));

            li.appendChild(checkbox);
            li.appendChild(textSpan);
            li.appendChild(deleteBtn);

            this.todosList.appendChild(li);
        });
    }

    updateCounter() {
        if (!this.todoCountEl) return;

        const activeCount = this.getActiveCount();
        const text = activeCount === 1 ? 'item' : 'items';
        this.todoCountEl.textContent = `${activeCount} ${text} left`;
    }

    setFilter(filter) {
        this.currentFilter = filter;
        this.renderTodos();

        if (this.filterButtons) {
            this.filterButtons.forEach(btn => {
                const isActive = btn.getAttribute('data-filter') === filter;
                btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
        }
    }
}

// Initialize the app only in browser
if (typeof document !== 'undefined' && document.readyState !== 'loading') {
    new TodoApp();
} else if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        new TodoApp();
    });
}

// Export for Node.js
if (typeof module !== 'undefined' && module.exports) {
    module.exports.TodoApp = TodoApp;
}
