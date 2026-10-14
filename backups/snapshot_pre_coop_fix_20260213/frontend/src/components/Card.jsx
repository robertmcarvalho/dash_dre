import React from 'react';

const Card = ({ title, children, className = '', icon: Icon, color = 'blue' }) => {
    const colorClasses = {
        blue: 'border-l-blue-500',
        green: 'border-l-green-500',
        red: 'border-l-red-500',
        purple: 'border-l-purple-500',
        yellow: 'border-l-yellow-500',
        gray: 'border-l-gray-500',
    };

    return (
        <div className={`bg-white p-6 rounded-xl shadow-sm border border-gray-100 ${colorClasses[color] ? `border-l-4 ${colorClasses[color]}` : ''} ${className}`}>
            {(title || Icon) && (
                <div className="flex items-center justify-between mb-4">
                    {title && <h3 className="text-gray-600 text-sm font-semibold uppercase tracking-wider">{title}</h3>}
                    {Icon && <Icon className={`w-5 h-5 text-${color}-500 opacity-80`} />}
                </div>
            )}
            <div className="text-gray-800">
                {children}
            </div>
        </div>
    );
};

export default Card;
