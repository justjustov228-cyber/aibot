import random
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

# ===================== ХРАНИЛИЩЕ СОСТОЯНИЙ В ПАМЯТИ =====================
# user_id -> game state dict
tictactoe_states = {}
guess_states = {}
quiz_states = {}
roulette_states = {}

# ===================== КРЕСТИКИ-НОЛИКИ =====================

def new_tictactoe_board():
    return [""] * 9


def render_tictactoe_keyboard(board, game_over=False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, cell in enumerate(board):
        text = cell if cell else "·"
        builder.button(text=text, callback_data=f"ttt_{i}" if not game_over else "ttt_over")
    builder.adjust(3, 3, 3)
    return builder.as_markup()


def check_winner(board):
    lines = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(board):
        return "draw"
    return None


def bot_move(board):
    empty = [i for i, c in enumerate(board) if not c]
    if not empty:
        return
    for i in empty:
        board[i] = "O"
        if check_winner(board) == "O":
            return
        board[i] = ""
    move = random.choice(empty)
    board[move] = "O"


def start_tictactoe(user_id: int):
    board = new_tictactoe_board()
    tictactoe_states[user_id] = board
    return board


def handle_tictactoe_move(user_id: int, index: int):
    board = tictactoe_states.get(user_id)
    if board is None:
        return None, None
    if board[index]:
        return board, None

    board[index] = "X"
    result = check_winner(board)
    if result is None:
        bot_move(board)
        result = check_winner(board)

    if result:
        tictactoe_states.pop(user_id, None)

    return board, result


# ===================== УГАДАЙ ЧИСЛО =====================

def start_guess(user_id: int, min_n=1, max_n=100):
    number = random.randint(min_n, max_n)
    guess_states[user_id] = {"number": number, "attempts": 0, "min": min_n, "max": max_n}


def handle_guess(user_id: int, guess: int):
    state = guess_states.get(user_id)
    if not state:
        return None

    state["attempts"] += 1
    number = state["number"]

    if guess == number:
        attempts = state["attempts"]
        guess_states.pop(user_id, None)
        return {"result": "win", "attempts": attempts}
    elif guess < number:
        return {"result": "higher"}
    else:
        return {"result": "lower"}


# ===================== КАМЕНЬ-НОЖНИЦЫ-БУМАГА =====================

RPS_OPTIONS = {"rock": "🪨 Камень", "scissors": "✂️ Ножницы", "paper": "📄 Бумага"}

RPS_BEATS = {
    "rock": "scissors",
    "scissors": "paper",
    "paper": "rock",
}


def rps_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in RPS_OPTIONS.items():
        builder.button(text=label, callback_data=f"rps_{key}")
    builder.adjust(3)
    return builder.as_markup()


def play_rps(user_choice: str):
    bot_choice = random.choice(list(RPS_OPTIONS.keys()))

    if user_choice == bot_choice:
        result = "draw"
    elif RPS_BEATS[user_choice] == bot_choice:
        result = "win"
    else:
        result = "lose"

    return bot_choice, result


# ===================== ВИКТОРИНА =====================

QUIZ_QUESTIONS = [
    {
        "question": "Какая планета самая большая в Солнечной системе?",
        "options": ["Земля", "Юпитер", "Сатурн", "Марс"],
        "correct": 1,
    },
    {
        "question": "Сколько континентов на Земле?",
        "options": ["5", "6", "7", "8"],
        "correct": 2,
    },
    {
        "question": "Кто написал «Войну и мир»?",
        "options": ["Достоевский", "Толстой", "Чехов", "Пушкин"],
        "correct": 1,
    },
    {
        "question": "Какой газ растения выделяют при фотосинтезе?",
        "options": ["Азот", "Углекислый газ", "Кислород", "Водород"],
        "correct": 2,
    },
    {
        "question": "В каком году человек впервые полетел в космос?",
        "options": ["1957", "1961", "1969", "1975"],
        "correct": 1,
    },
    {
        "question": "Сколько костей в теле взрослого человека?",
        "options": ["186", "206", "226", "246"],
        "correct": 1,
    },
    {
        "question": "Какая самая длинная река в мире?",
        "options": ["Амазонка", "Нил", "Янцзы", "Миссисипи"],
        "correct": 1,
    },
    {
        "question": "Сколько струн у классической гитары?",
        "options": ["4", "5", "6", "7"],
        "correct": 2,
    },
]


def quiz_keyboard(question_index: int) -> InlineKeyboardMarkup:
    q = QUIZ_QUESTIONS[question_index]
    builder = InlineKeyboardBuilder()
    for i, option in enumerate(q["options"]):
        builder.button(text=option, callback_data=f"quiz_{question_index}_{i}")
    builder.adjust(1)
    return builder.as_markup()


def start_quiz(user_id: int):
    questions = list(range(len(QUIZ_QUESTIONS)))
    random.shuffle(questions)
    quiz_states[user_id] = {"order": questions, "current": 0, "score": 0}
    return quiz_states[user_id]


def get_current_question(user_id: int):
    state = quiz_states.get(user_id)
    if not state:
        return None
    idx = state["order"][state["current"]]
    return idx, QUIZ_QUESTIONS[idx]


def answer_quiz(user_id: int, question_index: int, chosen_option: int):
    state = quiz_states.get(user_id)
    if not state:
        return None

    q = QUIZ_QUESTIONS[question_index]
    correct = chosen_option == q["correct"]
    if correct:
        state["score"] += 1

    state["current"] += 1
    finished = state["current"] >= len(state["order"])

    result = {
        "correct": correct,
        "correct_answer": q["options"][q["correct"]],
        "score": state["score"],
        "total": len(state["order"]),
        "finished": finished,
    }

    if finished:
        quiz_states.pop(user_id, None)

    return result


# ===================== РУЛЕТКА (символическая, безопасная) =====================

def roulette_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Крутить барабан", callback_data="roulette_spin")
    builder.button(text="🚪 Выйти из игры", callback_data="roulette_exit")
    builder.adjust(1)
    return builder.as_markup()


def start_roulette(user_id: int):
    roulette_states[user_id] = {"round": 1, "chamber": random.randint(1, 6)}


def spin_roulette(user_id: int):
    state = roulette_states.get(user_id)
    if not state:
        return None

    pull = random.randint(1, 6)
    is_loss = pull == state["chamber"]

    if is_loss:
        round_num = state["round"]
        roulette_states.pop(user_id, None)
        return {"result": "loss", "round": round_num}
    else:
        state["round"] += 1
        state["chamber"] = random.randint(1, 6)
        return {"result": "survive", "round": state["round"] - 1}
