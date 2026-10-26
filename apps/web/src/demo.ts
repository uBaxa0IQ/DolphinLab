import type { Project, ChatMessage } from './types'
export const demoProjects: Project[] = [
  { id: 'demo-retention', name: 'Отток клиентов', description: 'Предсказать отток клиентов сервиса. Найти устойчивую модель и понять, что влияет на уход.', status: 'running', demo: true, metric: 'ROC AUC', score: '0.8914',
    teams: [
      { id: 'baseline', name: 'Базовая модель', focus: 'Устойчивый результат', icon: 'layers', state: 'running', stage: 'Сравнение моделей', current: 'Сравниваем CatBoost и LightGBM на одинаковом разбиении.', updated: '2 мин назад', completed: 5, total: 7, members: [{ name: 'Алекс', role: 'Team lead', state: 'Координирует проверку', color: 'sage' }, { name: 'Мика', role: 'ML engineer', state: 'Обучает LightGBM', color: 'lilac' }, { name: 'Дана', role: 'Data engineer', state: 'Данные подготовлены', color: 'sand' }, { name: 'Рэй', role: 'Evaluator', state: 'Проверяет устойчивость', color: 'rose' }] },
      { id: 'features', name: 'Признаки и гипотезы', focus: 'Новое направление', icon: 'sparkles', state: 'running', stage: 'Проверка гипотезы', current: 'Проверяем, помогает ли динамика активности за последние 30 дней.', updated: 'только что', completed: 2, total: 5, members: [{ name: 'Ноа', role: 'Team lead', state: 'Планирует следующий опыт', color: 'sand' }, { name: 'Кай', role: 'ML engineer', state: 'Проверяет новые признаки', color: 'sage' }, { name: 'Ли', role: 'Data engineer', state: 'Строит агрегации', color: 'lilac' }, { name: 'Сэм', role: 'Evaluator', state: 'Ожидает эксперимент', color: 'rose' }] },
      { id: 'validation', name: 'Надёжность', focus: 'Независимая проверка', icon: 'shield', state: 'waiting', stage: 'Ожидание кандидата', current: 'Первый результат проверен. Следующая проверка — после сравнения моделей.', updated: '6 мин назад', completed: 3, total: 4, members: [{ name: 'Эли', role: 'Team lead', state: 'Ожидает кандидата', color: 'lilac' }, { name: 'Юн', role: 'ML engineer', state: 'Анализирует ошибки', color: 'sand' }, { name: 'Рен', role: 'Data engineer', state: 'Проверка данных завершена', color: 'rose' }, { name: 'Ари', role: 'Evaluator', state: 'Результат подтверждён', color: 'sage' }] },
    ],
    experiments: [
      { id: 'EXP-008', name: 'LightGBM · настроенные параметры', team: 'Базовая модель', status: 'running', score: '—', duration: '2 мин 14 с', detail: 'Обучение на пяти фолдах. Сравнение с текущим проверенным результатом.' },
      { id: 'EXP-007', name: 'CatBoost · поведенческие признаки', team: 'Признаки и гипотезы', status: 'verified', score: '0.8914', duration: '4 мин 38 с', detail: 'Демонстрационный результат. Evaluator проверил разбиение, отсутствие утечки и соответствие итогового файла.' },
      { id: 'EXP-006', name: 'CatBoost · исходные признаки', team: 'Базовая модель', status: 'verified', score: '0.8732', duration: '3 мин 12 с', detail: 'Демонстрационный baseline. Метрика посчитана на отложенной выборке.' },
      { id: 'EXP-005', name: 'Частотное кодирование категорий', team: 'Признаки и гипотезы', status: 'rejected', score: '0.8691', duration: '1 мин 56 с', detail: 'Гипотеза не улучшила baseline на одинаковом разбиении.' },
    ],
    events: [
      { id: 'e1', time: '14:42', title: 'Началось сравнение моделей', detail: 'Базовая модель · EXP-008', type: 'info' },
      { id: 'e2', time: '14:39', title: 'Новый лучший результат подтверждён', detail: 'ROC AUC 0.8914 · Надёжность', type: 'result' },
      { id: 'e3', time: '14:34', title: 'Выделено новое направление', detail: 'Признаки и гипотезы · техлид', type: 'team' },
    ],
    files: [{ id: 'f1', name: 'task.md', size: 2410, kind: 'Задача' }, { id: 'f2', name: 'train.csv', size: 24840192, kind: 'Данные' }, { id: 'f3', name: 'test.csv', size: 8321024, kind: 'Данные' }, { id: 'f4', name: 'sample_submission.csv', size: 153600, kind: 'Пример результата' }],
  },
  { id: 'demo-demand', name: 'Прогноз спроса', description: 'Оценить недельный спрос по магазинам и категориям.', status: 'completed', demo: true, metric: 'MAE', score: '12.48', teams: [], experiments: [], events: [{ id: 'd1', time: 'Вчера', title: 'Исследование завершено', detail: 'Итоговый результат проверен', type: 'result' }], files: [{ id: 'd2', name: 'task.md', size: 1800, kind: 'Задача' }] },
]
export const initialDemoMessages: Record<string, ChatMessage[]> = {
  'demo-retention': [
    { id: 'm1', role: 'user', content: 'Хочу предсказать отток. Важно, чтобы модель была устойчивой, а результат — объяснимым.', time: '14:12' },
    { id: 'm2', role: 'assistant', content: 'Принято. Техлид выделил три направления: базовая модель, новые признаки и независимая проверка. Начнём с рабочего результата, затем улучшим его.', time: '14:12' },
    { id: 'm3', role: 'assistant', content: 'Первый результат готов: ROC AUC 0.8732. Проверка пройдена, файл сохранён. Сейчас команда исследует поведенческие признаки.', time: '14:28' },
    { id: 'm4', role: 'user', content: 'Какой сейчас прогресс?', time: '14:40' },
    { id: 'm5', role: 'assistant', content: 'Есть улучшение до 0.8914 — evaluator подтвердил результат.\n\nДве команды продолжают исследование: сравнивают модели и проверяют динамику активности. Команда проверки ждёт следующего кандидата.', time: '14:40' },
  ],
  'demo-demand': [{ id: 'dm1', role: 'assistant', content: 'Исследование завершено. Демонстрационный результат: MAE 12.48. Здесь будет сохранена история обсуждений и принятых решений.', time: 'Вчера' }],
}
