import React from 'react';
import { Info } from 'lucide-react';

const InfoCard = ({ title, icon: Icon, content, bgColor = 'bg-blue-50', iconColor = 'text-blue-600', borderColor = 'border-blue-200' }) => {
    return (
        <div className={`${bgColor} border ${borderColor} rounded-lg p-4 shadow-sm hover:shadow-md transition-shadow`}>
            <div className="flex items-start gap-3">
                <div className={`${iconColor} mt-1 flex-shrink-0`}>
                    {Icon ? <Icon className="w-5 h-5" /> : <Info className="w-5 h-5" />}
                </div>
                <div className="flex-1">
                    <h3 className="font-semibold text-gray-900 mb-2">{title}</h3>
                    <p className="text-sm text-gray-700 leading-relaxed">{content}</p>
                </div>
            </div>
        </div>
    );
};

export default InfoCard;
